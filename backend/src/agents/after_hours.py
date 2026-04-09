"""
AfterHours Agent.

24/7 reception agent that handles after-hours contacts:
  - Answers FAQs from the business knowledge base
  - Handles emergency situations with safety guidance
  - Logs callback requests for the owner
  - Stays warm and reassuring even at 2am
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List

import anthropic

from backend.src.config import settings
from backend.src.models.event import InboundEvent
from backend.src.agents.prompts.after_hours import build_after_hours_prompt

logger = logging.getLogger(__name__)

AFTER_HOURS_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "send_sms",
        "description": "Send an SMS reply to the customer.",
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
        "name": "create_callback_request",
        "description": (
            "Log a callback request so the owner sees it on the dashboard "
            "first thing in the morning."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_phone": {"type": "string"},
                "customer_name": {"type": "string"},
                "issue_summary": {"type": "string", "description": "Brief description of what the customer needs."},
                "urgency": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "emergency"],
                },
            },
            "required": ["customer_phone", "issue_summary", "urgency"],
        },
    },
    {
        "name": "dispatch_emergency",
        "description": "Trigger emergency dispatch for urgent situations (burst pipe, flooding, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_phone": {"type": "string"},
                "customer_address": {"type": "string"},
                "issue_description": {"type": "string"},
            },
            "required": ["customer_phone", "issue_description"],
        },
    },
]


class AfterHoursAgent:
    """24/7 AI receptionist for after-hours customer contacts."""

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
        # Enrich business context with next opening time
        enriched_business = {**business}
        enriched_business["next_open"] = self._compute_next_open(business)
        enriched_business.setdefault("hours_summary", "Monday–Friday 8am–6pm, Saturday 9am–2pm")

        system_prompt = build_after_hours_prompt(enriched_business)
        messages: List[Dict[str, Any]] = [
            {"role": "user", "content": event.message_body or ""}
        ]

        outcome = "faq_answered"
        tool_calls_audit: List[Dict[str, Any]] = []
        final_reply = ""

        for iteration in range(self.MAX_ITERATIONS):
            import asyncio
            response = await asyncio.to_thread(
                self.client.messages.create,
                model=settings.ANTHROPIC_MODEL,
                max_tokens=settings.ANTHROPIC_MAX_TOKENS,
                system=system_prompt,
                tools=AFTER_HOURS_TOOLS,
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
                result_str = await self._execute_tool(block.name, block.input, event, store)

                if block.name == "dispatch_emergency":
                    outcome = "emergency_dispatched"
                elif block.name == "create_callback_request":
                    outcome = "callback_scheduled"

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str,
                })

            messages.append({"role": "user", "content": tool_results})

        # Log the interaction as a callback request if no tool did so
        if store and event.from_number and outcome == "faq_answered":
            await self._log_interaction(event, final_reply, store)

        return {"reply": final_reply, "outcome": outcome, "tool_calls": tool_calls_audit}

    async def _execute_tool(
        self, tool_name: str, tool_input: Dict[str, Any],
        event: InboundEvent, store: Any
    ) -> str:
        try:
            if tool_name == "send_sms":
                from backend.src.tools.sms import SMSTool
                result = await SMSTool().send(
                    to=tool_input["to_number"], body=tool_input["message"]
                )
                return json.dumps(result)

            elif tool_name == "create_callback_request":
                # Save as a note on the customer record
                if store:
                    customer = await store.get_customer(
                        tool_input.get("customer_phone", event.from_number or "")
                    )
                    if customer:
                        note = (
                            f"[Callback requested {datetime.utcnow().isoformat()}] "
                            f"{tool_input['issue_summary']} (urgency: {tool_input['urgency']})"
                        )
                        customer.notes = (customer.notes or "") + "\n" + note
                        await store.save_customer(customer)
                return json.dumps({"success": True, "callback_logged": True})

            elif tool_name == "dispatch_emergency":
                # In production: page on-call tech via PagerDuty / SMS
                logger.warning(
                    "EMERGENCY DISPATCH triggered for %s: %s",
                    tool_input.get("customer_phone"),
                    tool_input.get("issue_description"),
                )
                return json.dumps({
                    "success": True,
                    "dispatch_id": f"EMRG-{datetime.utcnow().timestamp():.0f}",
                    "eta_minutes": 90,
                    "_demo": True,
                })

            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as exc:
            logger.exception("AfterHours tool '%s' failed: %s", tool_name, exc)
            return json.dumps({"success": True, "_demo": True})

    async def _log_interaction(self, event: InboundEvent, reply: str, store: Any) -> None:
        """Ensure the customer record exists with a last-contact timestamp."""
        try:
            customer = await store.get_customer(event.from_number)
            if not customer:
                from backend.src.models.customer import Customer
                customer = Customer(
                    phone=event.from_number,
                    business_id=event.business_id,
                    first_contact_at=datetime.utcnow(),
                )
            customer.last_contact_at = datetime.utcnow()
            await store.save_customer(customer)
        except Exception as exc:
            logger.warning("Failed to log after-hours interaction: %s", exc)

    def _compute_next_open(self, business: Dict[str, Any]) -> str:
        """Return a human-readable string like 'Monday at 8:00 AM'."""
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(business.get("timezone", "America/Chicago"))
            now = datetime.now(tz)
        except Exception:
            now = datetime.utcnow()

        days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        hours = business.get("hours", {})
        if not hours:
            return "next business day"

        for offset in range(1, 8):
            day_idx = (now.weekday() + offset) % 7
            day_name = days[day_idx]
            day_h = hours.get(day_name)
            if not day_h:
                continue
            closed = day_h.closed if hasattr(day_h, "closed") else day_h.get("closed", False)
            if not closed:
                open_t = day_h.open if hasattr(day_h, "open") else day_h.get("open", "09:00")
                return f"{day_name.capitalize()} at {open_t}"

        return "next business day"
