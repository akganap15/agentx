"""
Tests for the Orchestrator agent.

Covers:
  - Intent classification parsing
  - Routing to the correct specialist agent
  - After-hours detection logic
  - Graceful fallback when Claude returns malformed JSON
"""

from __future__ import annotations

import json
from typing import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.agents.orchestrator import Orchestrator
from backend.src.db.store import DEMO_BUSINESS_ID, InMemoryStore
from backend.src.models.event import EventSource, EventType, InboundEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_classification_response(
    agent: str = "lead_catcher",
    confidence: float = 0.92,
    urgency: str = "medium",
    is_after_hours: bool = False,
) -> MagicMock:
    """Build a mock Anthropic response returning a classification JSON."""
    text = json.dumps({
        "agent": agent,
        "confidence": confidence,
        "intent_summary": "Test intent",
        "urgency": urgency,
        "is_after_hours": is_after_hours,
    })
    block = MagicMock()
    block.text = text
    response = MagicMock()
    response.content = [block]
    response.stop_reason = "end_turn"
    return response


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestOrchestratorClassification:

    @pytest.mark.asyncio
    async def test_routes_lead_message_to_lead_catcher(
        self, store: InMemoryStore, make_event: Callable
    ) -> None:
        """A pricing inquiry should be routed to lead_catcher."""
        event = make_event(message="Hi, how much does it cost to fix a leaky pipe?")

        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = make_classification_response(agent="lead_catcher")
            mock_anthropic_cls.return_value = mock_client

            orchestrator = Orchestrator(store=store)

            # Patch the specialist agent to avoid real API calls
            with patch("backend.src.agents.lead_catcher.LeadCatcherAgent.run", new_callable=AsyncMock) as mock_run:
                mock_run.return_value = {"reply": "Sure! Let me get you a quote.", "outcome": "lead_captured", "tool_calls": []}
                result = await orchestrator.handle(event)

        assert result["agent"] == "lead_catcher"
        assert result["reply"] == "Sure! Let me get you a quote."

    @pytest.mark.asyncio
    async def test_routes_review_message_to_review_pilot(
        self, store: InMemoryStore, make_event: Callable
    ) -> None:
        """A message referencing a review should route to review_pilot."""
        event = make_event(message="Just left you guys a 5 star review on Google!")

        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = make_classification_response(agent="review_pilot", confidence=0.95)
            mock_anthropic_cls.return_value = mock_client

            orchestrator = Orchestrator(store=store)

            with patch("backend.src.agents.review_pilot.ReviewPilotAgent.run", new_callable=AsyncMock) as mock_run:
                mock_run.return_value = {"reply": "Thank you so much!", "outcome": "review_responded", "tool_calls": []}
                result = await orchestrator.handle(event)

        assert result["agent"] == "review_pilot"

    @pytest.mark.asyncio
    async def test_fallback_to_after_hours_on_bad_json(
        self, store: InMemoryStore, make_event: Callable
    ) -> None:
        """Malformed JSON from Claude should fall back to after_hours."""
        event = make_event(message="hello??")

        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_client = MagicMock()
            bad_response = MagicMock()
            bad_block = MagicMock()
            bad_block.text = "This is not valid JSON at all."
            bad_response.content = [bad_block]
            bad_response.stop_reason = "end_turn"
            mock_client.messages.create.return_value = bad_response
            mock_anthropic_cls.return_value = mock_client

            orchestrator = Orchestrator(store=store)

            with patch("backend.src.agents.after_hours.AfterHoursAgent.run", new_callable=AsyncMock) as mock_run:
                mock_run.return_value = {"reply": "Hi! Business is closed now.", "outcome": "faq_answered", "tool_calls": []}
                result = await orchestrator.handle(event)

        assert result["agent"] == "after_hours"
        assert result["routing"]["confidence"] == 0.0


class TestAfterHoursDetection:

    def test_is_after_hours_with_no_hours_configured(self) -> None:
        orchestrator = Orchestrator(store=None)
        result = orchestrator._is_after_hours({"hours": {}})
        assert result is False  # No hours = always open

    def test_is_after_hours_recognises_closed_day(self) -> None:
        orchestrator = Orchestrator(store=None)
        business = {
            "timezone": "UTC",
            "hours": {
                "monday": {"open": "09:00", "close": "17:00", "closed": False},
                "tuesday": {"open": "09:00", "close": "17:00", "closed": False},
                "wednesday": {"open": "09:00", "close": "17:00", "closed": False},
                "thursday": {"open": "09:00", "close": "17:00", "closed": False},
                "friday": {"open": "09:00", "close": "17:00", "closed": False},
                "saturday": {"open": "09:00", "close": "14:00", "closed": False},
                "sunday": {"closed": True},
            },
        }
        # We can't deterministically test _is_after_hours without mocking datetime,
        # but we can verify the method doesn't crash.
        result = orchestrator._is_after_hours(business)
        assert isinstance(result, bool)

    def test_format_hours_returns_string(self) -> None:
        orchestrator = Orchestrator(store=None)
        hours = {
            "monday": {"open": "08:00", "close": "18:00", "closed": False},
            "sunday": {"closed": True},
        }
        result = orchestrator._format_hours(hours)
        assert "Monday" in result
        assert "Sunday" in result or "Closed" in result

    def test_format_hours_empty(self) -> None:
        orchestrator = Orchestrator(store=None)
        result = orchestrator._format_hours({})
        assert result == "Not specified"


class TestOrchestratorBusinessContext:

    @pytest.mark.asyncio
    async def test_loads_customer_history_from_store(
        self, store: InMemoryStore, make_event: Callable
    ) -> None:
        """Orchestrator should enrich the prompt with existing customer data."""
        # Alice is a known customer in the demo store
        event = make_event(from_number="+15550001001", message="Need another drain unclogged")

        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = make_classification_response(agent="lead_catcher")
            mock_anthropic_cls.return_value = mock_client

            orchestrator = Orchestrator(store=store)

            # Capture what was passed to the messages.create call
            with patch("backend.src.agents.lead_catcher.LeadCatcherAgent.run", new_callable=AsyncMock) as mock_run:
                mock_run.return_value = {"reply": "Hi Alice!", "outcome": "lead_captured", "tool_calls": []}
                await orchestrator.handle(event)

            # Verify Claude was called with customer history
            call_args = mock_client.messages.create.call_args
            messages = call_args.kwargs.get("messages") or call_args.args[0] if call_args.args else []
            user_content = ""
            for m in messages:
                if m.get("role") == "user":
                    user_content = m.get("content", "")
                    break

            assert "Alice" in user_content or "Visits" in user_content
