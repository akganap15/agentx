"""
Tests for the LeadCatcher agent.

Covers:
  - Full agentic loop execution with mocked Claude responses
  - Tool execution: check_calendar_availability, book_appointment, save_lead_notes
  - Outcome tracking (lead_captured → lead_qualified → appointment_booked)
  - Demo fallback when Google Calendar is not configured
  - Customer record updates after tool execution
"""

from __future__ import annotations

import json
from typing import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.agents.lead_catcher import LeadCatcherAgent
from backend.src.db.store import DEMO_BUSINESS_ID, InMemoryStore
from backend.src.models.event import EventSource, EventType, InboundEvent


# ---------------------------------------------------------------------------
# Helper: build Claude tool_use responses
# ---------------------------------------------------------------------------

def make_tool_use_response(
    tool_name: str,
    tool_input: dict,
    tool_use_id: str = "toolu_01",
) -> MagicMock:
    """Build a mock Anthropic response requesting a tool call."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = tool_input
    block.id = tool_use_id

    response = MagicMock()
    response.content = [block]
    response.stop_reason = "tool_use"
    return response


def make_end_turn_response(text: str = "Great! I've booked you in.") -> MagicMock:
    """Build a mock Anthropic end_turn response with text."""
    block = MagicMock()
    block.type = "text"
    block.text = text

    response = MagicMock()
    response.content = [block]
    response.stop_reason = "end_turn"
    return response


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLeadCatcherBasic:

    @pytest.mark.asyncio
    async def test_single_turn_response(
        self, store: InMemoryStore, make_event: Callable
    ) -> None:
        """Agent should return a reply when Claude responds with end_turn on first call."""
        event = make_event(message="How much to fix a leaky pipe?")
        business = {"id": DEMO_BUSINESS_ID, "name": "Pete's Plumbing", "industry": "plumbing"}

        with patch("anthropic.Anthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = make_end_turn_response(
                "Great question! Pipe repairs typically run $150–$350 depending on complexity."
            )
            mock_cls.return_value = mock_client

            agent = LeadCatcherAgent()
            result = await agent.run(event=event, business=business, store=store)

        assert "reply" in result
        assert "outcome" in result
        assert result["reply"] != ""
        assert mock_client.messages.create.call_count == 1

    @pytest.mark.asyncio
    async def test_outcome_defaults_to_lead_captured(
        self, store: InMemoryStore, make_event: Callable
    ) -> None:
        """Without tool calls, outcome should be 'lead_captured'."""
        event = make_event(message="Do you do emergency plumbing?")
        business = {"id": DEMO_BUSINESS_ID, "name": "Pete's Plumbing"}

        with patch("anthropic.Anthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = make_end_turn_response("Yes! We do 24/7 emergency work.")
            mock_cls.return_value = mock_client

            agent = LeadCatcherAgent()
            result = await agent.run(event=event, business=business, store=store)

        assert result["outcome"] == "lead_captured"
        assert result["tool_calls"] == []


class TestLeadCatcherToolLoop:

    @pytest.mark.asyncio
    async def test_check_calendar_tool_called(
        self, store: InMemoryStore, make_event: Callable
    ) -> None:
        """Agent should call check_calendar_availability when scheduling."""
        event = make_event(message="I'd like to schedule a repair.")
        business = {"id": DEMO_BUSINESS_ID, "name": "Pete's Plumbing", "industry": "plumbing"}

        # Sequence: tool_use (check_calendar) → end_turn
        calendar_response = make_tool_use_response(
            "check_calendar_availability",
            {"business_id": DEMO_BUSINESS_ID, "service_duration_minutes": 60},
            tool_use_id="toolu_cal_01",
        )
        final_response = make_end_turn_response(
            "I have Thursday at 9am or Friday at 2pm available. Which works for you?"
        )

        with patch("anthropic.Anthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = [calendar_response, final_response]
            mock_cls.return_value = mock_client

            agent = LeadCatcherAgent()
            result = await agent.run(event=event, business=business, store=store)

        assert mock_client.messages.create.call_count == 2
        assert any(t["tool"] == "check_calendar_availability" for t in result["tool_calls"])

    @pytest.mark.asyncio
    async def test_book_appointment_updates_outcome(
        self, store: InMemoryStore, make_event: Callable
    ) -> None:
        """Calling book_appointment should set outcome to 'appointment_booked'."""
        event = make_event(
            message="Thursday at 9am works!",
            from_number="+15550009888",
        )
        business = {"id": DEMO_BUSINESS_ID, "name": "Pete's Plumbing"}

        booking_response = make_tool_use_response(
            "book_appointment",
            {
                "business_id": DEMO_BUSINESS_ID,
                "customer_phone": "+15550009888",
                "customer_name": "Test Customer",
                "service_description": "Pipe repair",
                "appointment_datetime": "2025-03-28T09:00:00",
            },
            tool_use_id="toolu_book_01",
        )
        final_response = make_end_turn_response(
            "You're all set! See you Thursday March 28 at 9am for your pipe repair."
        )

        with patch("anthropic.Anthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = [booking_response, final_response]
            mock_cls.return_value = mock_client

            agent = LeadCatcherAgent()
            result = await agent.run(event=event, business=business, store=store)

        assert result["outcome"] == "appointment_booked"

    @pytest.mark.asyncio
    async def test_save_lead_notes_creates_customer_record(
        self, store: InMemoryStore, make_event: Callable
    ) -> None:
        """save_lead_notes should create or update the customer in the store."""
        new_phone = "+15550008888"
        event = make_event(from_number=new_phone, message="I need bathroom plumbing for a remodel.")
        business = {"id": DEMO_BUSINESS_ID, "name": "Pete's Plumbing"}

        # Verify customer doesn't exist yet
        assert await store.get_customer(new_phone) is None

        notes_response = make_tool_use_response(
            "save_lead_notes",
            {
                "customer_phone": new_phone,
                "notes": "Interested in bathroom remodel plumbing. Budget ~$3k.",
                "lead_stage": "qualified",
            },
            tool_use_id="toolu_notes_01",
        )
        final_response = make_end_turn_response(
            "Got it! I've noted your project details. Would you like to schedule a free estimate?"
        )

        with patch("anthropic.Anthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = [notes_response, final_response]
            mock_cls.return_value = mock_client

            agent = LeadCatcherAgent()
            result = await agent.run(event=event, business=business, store=store)

        # Customer should now exist in store
        customer = await store.get_customer(new_phone)
        assert customer is not None
        assert customer.lead_stage == "qualified"
        assert "bathroom remodel" in (customer.notes or "")
        assert result["outcome"] == "lead_qualified"


class TestLeadCatcherSafety:

    @pytest.mark.asyncio
    async def test_max_iterations_prevents_infinite_loop(
        self, store: InMemoryStore, make_event: Callable
    ) -> None:
        """Agent must stop after MAX_ITERATIONS even if Claude keeps requesting tools."""
        event = make_event(message="Book me a slot.")
        business = {"id": DEMO_BUSINESS_ID, "name": "Pete's Plumbing"}

        # Always returns tool_use — would loop forever without the limit
        infinite_tool_response = make_tool_use_response(
            "check_calendar_availability",
            {"business_id": DEMO_BUSINESS_ID},
        )

        with patch("anthropic.Anthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = infinite_tool_response
            mock_cls.return_value = mock_client

            agent = LeadCatcherAgent()
            result = await agent.run(event=event, business=business, store=store)

        # Should stop at MAX_ITERATIONS
        assert mock_client.messages.create.call_count <= LeadCatcherAgent.MAX_ITERATIONS
        assert "reply" in result
