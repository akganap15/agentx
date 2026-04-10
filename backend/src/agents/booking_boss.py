"""
BookingBoss Agent.

Handles appointment lifecycle events:
  - No-show follow-up: reach out to customers who missed their appointment
  - Waitlist management: fill cancelled slots from the waitlist
  - Appointment reminders: proactive 48hr and 2hr reminders
  - Cancellation handling: reschedule rather than lose the customer
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

from backend.src.config import settings
from backend.src.models.event import InboundEvent
from backend.src.agents.prompts.booking_boss import build_booking_boss_prompt
from backend.src.agents.litellm_client import litellm_chat

logger = logging.getLogger(__name__)

BOOKING_BOSS_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "check_calendar_availability",
        "description": "Check open appointment slots for rebooking.",
        "input_schema": {
            "type": "object",
            "properties": {
                "business_id": {"type": "string"},
                "service_duration_minutes": {"type": "integer", "default": 60},
            },
            "required": ["business_id"],
        },
    },
    {
        "name": "book_appointment",
        "description": "Book a new or rescheduled appointment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "business_id": {"type": "string"},
                "customer_phone": {"type": "string"},
                "customer_name": {"type": "string"},
                "service_description": {"type": "string"},
                "appointment_datetime": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["business_id", "customer_phone", "appointment_datetime", "service_description"],
        },
    },
    {
        "name": "cancel_appointment",
        "description": "Cancel an existing appointment and open the slot for waitlist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "appointment_id": {"type": "string"},
                "reason": {"type": "string"},
                "notify_waitlist": {"type": "boolean", "default": True},
            },
            "required": ["appointment_id"],
        },
    },
    {
        "name": "get_waitlist",
        "description": "Get the waitlist for a given time slot or service.",
        "input_schema": {
            "type": "object",
            "properties": {
                "business_id": {"type": "string"},
                "service": {"type": "string"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["business_id"],
        },
    },
    {
        "name": "send_sms",
        "description": "Send SMS to the customer.",
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


class BookingBossAgent:
    """Manages the appointment lifecycle — no-shows, waitlists, reminders."""

    MAX_ITERATIONS = 6

    def __init__(self) -> None:
        pass

    async def run(
        self,
        event: InboundEvent,
        business: Dict[str, Any],
        store: Any = None,
        history: List[Dict[str, Any]] = [],
    ) -> Dict[str, Any]:
        system_prompt = build_booking_boss_prompt(business)

        # Provide booking context
        context = event.message_body or ""
        raw = event.raw_payload or {}
        if raw.get("appointment_id"):
            context = (
                f"Context: Customer missed appointment #{raw['appointment_id']} "
                f"on {raw.get('appointment_datetime', 'recently')} for {raw.get('service', 'service')}.\n\n"
                f"Customer message: {context}"
            )

        # Rebuild from history for multi-turn
        messages: List[Dict[str, Any]] = []
        for turn in history[:-1]:
            role = "user" if turn["role"] == "customer" else "assistant"
            messages.append({"role": role, "content": turn["content"]})
        messages.append({"role": "user", "content": context})
        outcome = "booking_handled"
        tool_calls_audit: List[Dict[str, Any]] = []
        final_reply = ""

        for iteration in range(self.MAX_ITERATIONS):
            response = await litellm_chat(
                model=settings.LITELLM_MODEL,
                max_tokens=settings.ANTHROPIC_MAX_TOKENS,
                system=system_prompt,
                tools=BOOKING_BOSS_TOOLS,
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

                if block.name == "book_appointment":
                    outcome = "no_show_recovered"

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str,
                })

            messages.append({"role": "user", "content": tool_results})

        return {"reply": final_reply, "outcome": outcome, "tool_calls": tool_calls_audit}

    async def _execute_tool(
        self, tool_name: str, tool_input: Dict[str, Any],
        event: InboundEvent, store: Any
    ) -> str:
        try:
            if tool_name == "check_calendar_availability":
                return await self._check_calendar(tool_input)
            elif tool_name == "book_appointment":
                return await self._book_appointment(tool_input, store)
            elif tool_name == "cancel_appointment":
                return await self._cancel_appointment(tool_input, store)
            elif tool_name == "get_waitlist":
                return await self._get_waitlist(tool_input, store)
            elif tool_name == "send_sms":
                from backend.src.tools.sms import SMSTool
                result = await SMSTool().send(to=tool_input["to_number"], body=tool_input["message"])
                return json.dumps(result)
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as exc:
            logger.exception("BookingBoss tool '%s' failed: %s", tool_name, exc)
            return json.dumps({"error": str(exc)})

    async def _check_calendar(self, params: Dict[str, Any]) -> str:
        """Return available slots via Google Calendar (falls back to demo if no key)."""
        from backend.src.tools.calendar import CalendarTool
        tool = CalendarTool()
        slots = await tool.get_availability(
            business_id=params.get("business_id", ""),
            duration_minutes=params.get("service_duration_minutes", 60),
        )
        return json.dumps({"available_slots": slots})

    async def _book_appointment(self, params: Dict[str, Any], store: Any) -> str:
        """Book via Google Calendar and update customer record in store."""
        from backend.src.tools.calendar import CalendarTool
        tool = CalendarTool()
        event_id = await tool.book_appointment(
            business_id=params.get("business_id", ""),
            customer_phone=params.get("customer_phone", ""),
            customer_name=params.get("customer_name", ""),
            service=params.get("service_description", "Appointment"),
            appointment_dt=params["appointment_datetime"],
            notes=params.get("notes", ""),
        )
        if store and params.get("customer_phone"):
            customer = await store.get_customer(params["customer_phone"])
            if customer:
                customer.lead_stage = "appointment_booked"
                customer.upcoming_appointment = datetime.fromisoformat(params["appointment_datetime"])
                customer.last_contact_at = datetime.utcnow()
                await store.save_customer(customer)
        return json.dumps({
            "success": True,
            "calendar_event_id": event_id,
            "appointment_datetime": params["appointment_datetime"],
            "confirmation_message": f"Appointment rescheduled for {params['appointment_datetime']}",
        })

    async def _cancel_appointment(self, params: Dict[str, Any], store: Any) -> str:
        """Delete the calendar event and notify waitlisted customers via SMS."""
        from backend.src.tools.calendar import CalendarTool
        from backend.src.tools.sms import SMSTool

        appointment_id = params.get("appointment_id", "")
        notify_waitlist = params.get("notify_waitlist", True)

        cancelled = await CalendarTool().cancel_event(appointment_id)

        notified = 0
        if cancelled and notify_waitlist and store:
            customers = await store.list_customers()
            waitlisted = [
                c for c in customers
                if c.lead_stage in ("waitlist", "new") and c.opted_in_sms
            ][:3]
            sms = SMSTool()
            for c in waitlisted:
                await sms.send(
                    to=c.phone,
                    body="Good news — a slot just opened up! Reply to book your appointment.",
                )
                notified += 1

        return json.dumps({
            "success": cancelled,
            "appointment_id": appointment_id,
            "waitlist_notified": notified,
        })

    async def _get_waitlist(self, params: Dict[str, Any], store: Any) -> str:
        """Query the customer store for leads without a booked appointment."""
        if not store:
            return json.dumps({"waitlist": [], "total": 0})

        business_id = params.get("business_id", "")
        limit = params.get("limit", 5)
        customers = await store.list_customers(business_id=business_id or None)

        waitlist = [
            {
                "phone": c.phone,
                "name": c.name or "Customer",
                "lead_stage": c.lead_stage,
                "last_contact_at": c.last_contact_at.isoformat() if c.last_contact_at else None,
            }
            for c in customers
            if c.opted_in_sms
            and c.is_lead
            and c.lead_stage not in ("appointment_booked", "closed")
            and c.upcoming_appointment is None
        ][:limit]

        return json.dumps({"waitlist": waitlist, "total": len(waitlist)})
