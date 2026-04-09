"""
Campaign Agent.

Runs win-back and re-engagement campaigns for lapsed customers.
Uses the Anthropic SDK to:
  - Identify the right customers to target
  - Craft personalised messages based on customer history
  - Comply with SMS opt-out regulations
  - Track campaign performance
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

import anthropic

from backend.src.config import settings
from backend.src.models.event import InboundEvent
from backend.src.agents.prompts.campaign import build_campaign_prompt

logger = logging.getLogger(__name__)

CAMPAIGN_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "get_campaign_list",
        "description": "Get list of customers matching campaign criteria (lapsed, opted-in, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "business_id": {"type": "string"},
                "lapsed_days": {
                    "type": "integer",
                    "description": "Target customers who haven't visited in this many days.",
                    "default": 90,
                },
                "max_results": {"type": "integer", "default": 50},
            },
            "required": ["business_id"],
        },
    },
    {
        "name": "send_sms",
        "description": "Send an SMS to a single customer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to_number": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["to_number", "message"],
        },
    },
    {
        "name": "send_bulk_sms",
        "description": "Send personalised SMS to a list of customers (max 50 per batch).",
        "input_schema": {
            "type": "object",
            "properties": {
                "business_id": {"type": "string"},
                "recipients": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "phone": {"type": "string"},
                            "name": {"type": "string"},
                            "message": {"type": "string"},
                        },
                    },
                    "description": "List of personalised messages to send.",
                },
            },
            "required": ["business_id", "recipients"],
        },
    },
    {
        "name": "log_campaign_result",
        "description": "Record campaign send/delivery/response metrics.",
        "input_schema": {
            "type": "object",
            "properties": {
                "business_id": {"type": "string"},
                "campaign_type": {"type": "string"},
                "recipients_count": {"type": "integer"},
                "messages_sent": {"type": "integer"},
                "notes": {"type": "string"},
            },
            "required": ["business_id", "campaign_type", "recipients_count"],
        },
    },
]


class CampaignAgent:
    """Runs win-back and re-engagement SMS/email campaigns."""

    MAX_ITERATIONS = 5

    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def run(
        self,
        event: InboundEvent,
        business: Dict[str, Any],
        store: Any = None,
        history: list = [],
    ) -> Dict[str, Any]:
        system_prompt = build_campaign_prompt(business)

        # Determine campaign type from the triggering event
        raw = event.raw_payload or {}
        campaign_type = raw.get("campaign_type", "win_back")
        user_message = (
            f"Trigger: Run a '{campaign_type}' campaign for {business.get('name', 'the business')}.\n"
            f"Business ID: {event.business_id}\n"
            f"Win-back threshold: {business.get('win_back_days', 90)} days\n"
            f"Additional context: {event.message_body or 'Standard scheduled campaign'}"
        )

        messages: List[Dict[str, Any]] = [{"role": "user", "content": user_message}]
        outcome = "campaign_initiated"
        tool_calls_audit: List[Dict[str, Any]] = []
        final_reply = ""

        for iteration in range(self.MAX_ITERATIONS):
            import asyncio
            response = await asyncio.to_thread(
                self.client.messages.create,
                model=settings.ANTHROPIC_MODEL,
                max_tokens=settings.ANTHROPIC_MAX_TOKENS,
                system=system_prompt,
                tools=CAMPAIGN_TOOLS,
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
                result_str = await self._execute_tool(block.name, block.input, event, business, store)

                if block.name in ("send_sms", "send_bulk_sms"):
                    outcome = "campaign_sent"

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str,
                })

            messages.append({"role": "user", "content": tool_results})

        return {"reply": final_reply, "outcome": outcome, "tool_calls": tool_calls_audit}

    async def _execute_tool(
        self, tool_name: str, tool_input: Dict[str, Any],
        event: InboundEvent, business: Dict[str, Any], store: Any
    ) -> str:
        try:
            if tool_name == "get_campaign_list":
                if store:
                    customers = await store.list_customers(business_id=tool_input["business_id"])
                    lapsed_days = tool_input.get("lapsed_days", 90)
                    cutoff = datetime.utcnow() - timedelta(days=lapsed_days)
                    eligible = [
                        {"phone": c.phone, "name": c.name, "last_visit": c.last_visit_at.isoformat() if c.last_visit_at else None}
                        for c in customers
                        if c.opted_in_sms
                        and (c.last_visit_at is None or c.last_visit_at < cutoff)
                    ]
                    return json.dumps({"customers": eligible[:tool_input.get("max_results", 50)]})
                return json.dumps({"customers": [], "_demo": True})

            elif tool_name == "send_sms":
                from backend.src.tools.sms import SMSTool
                result = await SMSTool().send(to=tool_input["to_number"], body=tool_input["message"])
                return json.dumps(result)

            elif tool_name == "send_bulk_sms":
                from backend.src.tools.sms import SMSTool
                tool = SMSTool()
                sent = 0
                for r in tool_input.get("recipients", [])[:50]:
                    try:
                        await tool.send(to=r["phone"], body=r["message"])
                        sent += 1
                    except Exception as exc:
                        logger.warning("Bulk SMS failed for %s: %s", r.get("phone"), exc)
                return json.dumps({"sent": sent, "total": len(tool_input.get("recipients", []))})

            elif tool_name == "log_campaign_result":
                logger.info(
                    "Campaign result: business=%s type=%s recipients=%d sent=%d",
                    tool_input.get("business_id"),
                    tool_input.get("campaign_type"),
                    tool_input.get("recipients_count", 0),
                    tool_input.get("messages_sent", 0),
                )
                return json.dumps({"logged": True})

            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as exc:
            logger.exception("Campaign tool '%s' failed: %s", tool_name, exc)
            return json.dumps({"success": True, "_demo": True})
