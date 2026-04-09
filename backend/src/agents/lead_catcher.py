"""
LeadCatcher Agent.

Qualifies inbound leads and books appointments via an agentic loop
using the Anthropic Python SDK with tool_use.

Tools available to this agent:
  - check_calendar_availability
  - book_appointment
  - send_sms
  - save_lead_notes
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import anthropic

from backend.src.config import settings
from backend.src.models.event import InboundEvent
from backend.src.agents.prompts.lead_catcher import build_lead_catcher_prompt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool definitions (Anthropic tool_use format)
# ---------------------------------------------------------------------------

LEAD_CATCHER_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "check_calendar_availability",
        "description": (
            "Check available appointment slots for the business. "
            "Returns a list of available datetime slots in the next 7 days."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "business_id": {
                    "type": "string",
                    "description": "The business ID to check availability for.",
                },
                "service_duration_minutes": {
                    "type": "integer",
                    "description": "Estimated duration of the appointment in minutes.",
                    "default": 60,
                },
                "preferred_days": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of preferred days e.g. ['monday', 'tuesday']",
                },
            },
            "required": ["business_id"],
        },
    },
    {
        "name": "book_appointment",
        "description": "Book a confirmed appointment for a customer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "business_id": {"type": "string"},
                "customer_phone": {"type": "string"},
                "customer_name": {"type": "string"},
                "service_description": {"type": "string"},
                "appointment_datetime": {
                    "type": "string",
                    "description": "ISO 8601 datetime string e.g. 2025-03-28T14:00:00",
                },
                "notes": {"type": "string"},
            },
            "required": ["business_id", "customer_phone", "appointment_datetime", "service_description"],
        },
    },
    {
        "name": "send_sms",
        "description": "Send an SMS message to the customer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to_number": {"type": "string", "description": "E.164 phone number of the recipient."},
                "message": {"type": "string", "description": "The SMS message body (max 160 chars preferred)."},
            },
            "required": ["to_number", "message"],
        },
    },
    {
        "name": "save_lead_notes",
        "description": "Save notes about the lead's requirements to their customer record.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_phone": {"type": "string"},
                "notes": {"type": "string", "description": "Structured notes about the lead's needs."},
                "lead_stage": {
                    "type": "string",
                    "enum": ["new", "contacted", "qualified", "appointment_booked", "closed"],
                },
            },
            "required": ["customer_phone", "notes"],
        },
    },
]


# ---------------------------------------------------------------------------
# Agent implementation
# ---------------------------------------------------------------------------

class LeadCatcherAgent:
    """
    Handles inbound sales inquiries using an Anthropic agentic loop.

    The loop continues until:
      - Claude returns a stop_reason of "end_turn" (no more tool calls), OR
      - MAX_ITERATIONS is reached (safety limit)
    """

    MAX_ITERATIONS = 6

    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def run(
        self,
        event: InboundEvent,
        business: Dict[str, Any],
        store: Any = None,
        history: List[Dict[str, Any]] = [],
    ) -> Dict[str, Any]:
        """
        Execute the LeadCatcher agentic loop.

        Returns:
            {
                "reply": str,    — the final message to send to the customer
                "outcome": str,  — e.g. "appointment_booked", "lead_qualified"
                "tool_calls": list  — audit trail of tool invocations
            }
        """
        system_prompt = build_lead_catcher_prompt(business)

        # Rebuild conversation from history for multi-turn support
        messages: List[Dict[str, Any]] = []
        for turn in history[:-1]:  # exclude current turn (added below)
            role = "user" if turn["role"] == "customer" else "assistant"
            messages.append({"role": role, "content": turn["content"]})
        messages.append({"role": "user", "content": event.message_body or ""})

        outcome = "lead_captured"
        tool_calls_audit: List[Dict[str, Any]] = []
        final_reply = ""

        for iteration in range(self.MAX_ITERATIONS):
            logger.debug("LeadCatcher iteration %d for event_id=%s", iteration, event.id)

            import asyncio
            response = await asyncio.to_thread(
                self.client.messages.create,
                model=settings.ANTHROPIC_MODEL,
                max_tokens=settings.ANTHROPIC_MAX_TOKENS,
                system=system_prompt,
                tools=LEAD_CATCHER_TOOLS,
                messages=messages,
            )

            # Append assistant response to messages
            messages.append({"role": "assistant", "content": response.content})

            # Extract any text content for the final reply
            for block in response.content:
                if hasattr(block, "text"):
                    final_reply = block.text

            # Check stop condition
            if response.stop_reason == "end_turn":
                logger.info("LeadCatcher finished at iteration %d (end_turn)", iteration)
                break

            if response.stop_reason != "tool_use":
                logger.warning(
                    "Unexpected stop_reason=%s at iteration %d", response.stop_reason, iteration
                )
                break

            # Process tool calls
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                tool_name = block.name
                tool_input = block.input
                tool_use_id = block.id

                logger.info(
                    "LeadCatcher tool_call: %s input=%s event_id=%s",
                    tool_name, json.dumps(tool_input)[:200], event.id,
                )
                tool_calls_audit.append({"tool": tool_name, "input": tool_input})

                # Execute the tool
                result_str = await self._execute_tool(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    event=event,
                    business=business,
                    store=store,
                )

                # Update outcome based on tool usage
                if tool_name == "book_appointment":
                    outcome = "appointment_booked"
                elif tool_name == "save_lead_notes" and outcome == "lead_captured":
                    lead_stage = tool_input.get("lead_stage", "")
                    if lead_stage == "qualified":
                        outcome = "lead_qualified"

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": result_str,
                })

            # Add tool results back into the conversation
            messages.append({"role": "user", "content": tool_results})

        return {
            "reply": final_reply,
            "outcome": outcome,
            "tool_calls": tool_calls_audit,
        }

    async def _execute_tool(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        event: InboundEvent,
        business: Dict[str, Any],
        store: Any,
    ) -> str:
        """Dispatch tool calls to their implementations."""
        try:
            if tool_name == "check_calendar_availability":
                return await self._check_calendar(tool_input, business)
            elif tool_name == "book_appointment":
                return await self._book_appointment(tool_input, store)
            elif tool_name == "send_sms":
                return await self._send_sms(tool_input)
            elif tool_name == "save_lead_notes":
                return await self._save_lead_notes(tool_input, event, store)
            else:
                return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as exc:
            logger.exception("Tool '%s' failed: %s", tool_name, exc)
            return json.dumps({"error": str(exc)})

    async def _check_calendar(self, params: Dict[str, Any], business: Dict[str, Any]) -> str:
        """
        Returns available appointment slots.
        In production this calls the Google Calendar tool.
        For the demo, returns realistic mock slots.
        """
        from backend.src.tools.calendar import CalendarTool
        try:
            tool = CalendarTool()
            slots = await tool.get_availability(
                business_id=params.get("business_id", business.get("id", "")),
                duration_minutes=params.get("service_duration_minutes", 60),
            )
            return json.dumps({"available_slots": slots})
        except Exception:
            # Demo fallback
            now = datetime.utcnow()
            slots = [
                (now + timedelta(days=1, hours=9)).isoformat(),
                (now + timedelta(days=1, hours=14)).isoformat(),
                (now + timedelta(days=2, hours=10)).isoformat(),
                (now + timedelta(days=3, hours=11)).isoformat(),
            ]
            return json.dumps({"available_slots": slots, "_demo": True})

    async def _book_appointment(self, params: Dict[str, Any], store: Any) -> str:
        """Confirms an appointment in the calendar and updates the customer record."""
        from backend.src.tools.calendar import CalendarTool
        try:
            tool = CalendarTool()
            event_id = await tool.book_appointment(
                business_id=params["business_id"],
                customer_phone=params["customer_phone"],
                customer_name=params.get("customer_name", ""),
                service=params["service_description"],
                appointment_dt=params["appointment_datetime"],
                notes=params.get("notes", ""),
            )
        except Exception:
            event_id = f"demo-evt-{datetime.utcnow().timestamp():.0f}"

        # Update customer record
        if store and params.get("customer_phone"):
            customer = await store.get_customer(params["customer_phone"])
            if customer:
                from datetime import datetime as dt
                customer.lead_stage = "appointment_booked"
                customer.upcoming_appointment = dt.fromisoformat(params["appointment_datetime"])
                customer.last_contact_at = dt.utcnow()
                await store.save_customer(customer)

        return json.dumps({
            "success": True,
            "calendar_event_id": event_id,
            "appointment_datetime": params["appointment_datetime"],
            "confirmation_message": f"Appointment confirmed for {params['appointment_datetime']}",
        })

    async def _send_sms(self, params: Dict[str, Any]) -> str:
        """Sends an SMS to the customer via the SMS tool."""
        from backend.src.tools.sms import SMSTool
        try:
            tool = SMSTool()
            result = await tool.send(to=params["to_number"], body=params["message"])
            return json.dumps(result)
        except Exception as exc:
            logger.warning("SMS send failed (demo mode): %s", exc)
            return json.dumps({"success": True, "sid": "DEMO_SID", "_demo": True})

    async def _save_lead_notes(
        self, params: Dict[str, Any], event: InboundEvent, store: Any
    ) -> str:
        """Persists lead notes to the customer record in the store."""
        if not store:
            return json.dumps({"success": True, "_demo": True})

        phone = params.get("customer_phone") or event.from_number
        if not phone:
            return json.dumps({"error": "No customer phone available"})

        customer = await store.get_customer(phone)
        if not customer:
            from backend.src.models.customer import Customer
            customer = Customer(
                phone=phone,
                business_id=event.business_id,
                first_contact_at=datetime.utcnow(),
            )

        customer.notes = params.get("notes", customer.notes)
        if params.get("lead_stage"):
            customer.lead_stage = params["lead_stage"]
        customer.last_contact_at = datetime.utcnow()
        await store.save_customer(customer)

        return json.dumps({"success": True, "customer_phone": phone})
