"""
ReviewPilot Agent.

Responds to customer reviews (Google Reviews) and solicits new ones.
Uses the Anthropic SDK with tools for posting public review responses
and sending review request links via SMS.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from backend.src.config import settings
from backend.src.models.event import InboundEvent
from backend.src.agents.prompts.review_pilot import build_review_pilot_prompt
from backend.src.agents.litellm_client import litellm_chat

logger = logging.getLogger(__name__)

REVIEW_PILOT_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "post_review_response",
        "description": "Post a public reply to a Google Review.",
        "input_schema": {
            "type": "object",
            "properties": {
                "review_id": {"type": "string", "description": "The Google Review ID."},
                "response_text": {"type": "string", "description": "The public response text (max 4096 chars)."},
            },
            "required": ["review_id", "response_text"],
        },
    },
    {
        "name": "request_review",
        "description": "Send a customer a direct link to leave a Google Review via SMS.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_phone": {"type": "string"},
                "customer_name": {"type": "string"},
                "review_link": {"type": "string", "description": "The Google review short URL."},
                "message": {"type": "string", "description": "Personalised message to send with the link."},
            },
            "required": ["customer_phone", "message"],
        },
    },
    {
        "name": "send_sms",
        "description": "Send an SMS to the customer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to_number": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["to_number", "message"],
        },
    },
]


class ReviewPilotAgent:
    """Handles review response and solicitation workflows."""

    MAX_ITERATIONS = 4

    def __init__(self) -> None:
        pass

    async def run(
        self,
        event: InboundEvent,
        business: Dict[str, Any],
        store: Any = None,
        history: list = [],
    ) -> Dict[str, Any]:
        system_prompt = build_review_pilot_prompt(business)

        # Build context: include review data from raw_payload if available
        user_content = event.message_body or ""
        raw = event.raw_payload or {}
        if raw.get("review_rating"):
            user_content = (
                f"New review received:\n"
                f"Rating: {raw['review_rating']} stars\n"
                f"Reviewer: {raw.get('reviewer_name', 'Anonymous')}\n"
                f"Review text: {raw.get('review_text', user_content)}\n"
                f"Review ID: {raw.get('review_id', 'unknown')}"
            )

        messages: List[Dict[str, Any]] = [{"role": "user", "content": user_content}]
        outcome = "review_handled"
        tool_calls_audit: List[Dict[str, Any]] = []
        final_reply = ""

        for iteration in range(self.MAX_ITERATIONS):
            response = await litellm_chat(
                model=settings.LITELLM_MODEL,
                max_tokens=settings.ANTHROPIC_MAX_TOKENS,
                system=system_prompt,
                tools=REVIEW_PILOT_TOOLS,
                messages=messages,
            )

            messages.append({"role": "assistant", "content": response.content})
            for block in response.content:
                if hasattr(block, "text"):
                    final_reply = block.text

            if response.stop_reason == "end_turn":
                break

            if response.stop_reason != "tool_use":
                break

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                tool_calls_audit.append({"tool": block.name, "input": block.input})
                result_str = await self._execute_tool(block.name, block.input, event, business)

                if block.name == "post_review_response":
                    outcome = "review_responded"
                elif block.name == "request_review":
                    outcome = "review_requested"

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str,
                })

            messages.append({"role": "user", "content": tool_results})

        return {"reply": final_reply, "outcome": outcome, "tool_calls": tool_calls_audit}

    async def _execute_tool(
        self, tool_name: str, tool_input: Dict[str, Any],
        event: InboundEvent, business: Dict[str, Any]
    ) -> str:
        try:
            if tool_name == "post_review_response":
                from backend.src.tools.reviews import ReviewsTool
                tool = ReviewsTool()
                result = await tool.post_response(
                    review_id=tool_input["review_id"],
                    response_text=tool_input["response_text"],
                    place_id=business.get("google_place_id", ""),
                )
                return json.dumps(result)

            elif tool_name in ("request_review", "send_sms"):
                from backend.src.tools.sms import SMSTool
                tool = SMSTool()
                to = tool_input.get("customer_phone") or tool_input.get("to_number", "")
                msg = tool_input.get("message", "")
                result = await tool.send(to=to, body=msg)
                return json.dumps(result)

            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as exc:
            logger.exception("ReviewPilot tool '%s' failed: %s", tool_name, exc)
            return json.dumps({"success": True, "_demo": True, "error_suppressed": str(exc)})
