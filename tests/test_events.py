"""
Tests for the /events API endpoints.

Covers:
  - POST /api/v1/events/simulate — happy path, validates full response schema
  - POST /api/v1/events/inbound  — webhook acceptance and background dispatch
  - Input validation edge cases
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch

from backend.src.db.store import DEMO_BUSINESS_ID


class TestSimulateEndpoint:
    """Tests for the /simulate convenience endpoint."""

    @pytest.mark.asyncio
    async def test_simulate_returns_200_with_reply(self, client: AsyncClient) -> None:
        """Happy path: simulate an SMS event and get a reply."""
        with patch(
            "backend.src.agents.orchestrator.Orchestrator.handle", new_callable=AsyncMock
        ) as mock_handle:
            mock_handle.return_value = {
                "agent": "lead_catcher",
                "reply": "Hi! Thanks for reaching out to Pete's Plumbing.",
                "outcome": "lead_captured",
                "tool_calls": [],
                "routing": {"agent": "lead_catcher", "confidence": 0.9},
            }

            response = await client.post(
                "/api/v1/events/simulate",
                json={
                    "message": "Hi, I have a leaky faucet. How much to fix?",
                    "from_number": "+15550009999",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "processed"
        assert "event_id" in data
        assert data["agent_reply"] == "Hi! Thanks for reaching out to Pete's Plumbing."

    @pytest.mark.asyncio
    async def test_simulate_uses_demo_business_id_by_default(self, client: AsyncClient) -> None:
        """Business ID should default to DEMO_BUSINESS_ID when not provided."""
        with patch(
            "backend.src.agents.orchestrator.Orchestrator.handle", new_callable=AsyncMock
        ) as mock_handle:
            mock_handle.return_value = {
                "agent": "after_hours",
                "reply": "We're closed right now.",
                "outcome": "faq_answered",
                "tool_calls": [],
                "routing": {},
            }

            response = await client.post(
                "/api/v1/events/simulate",
                json={"message": "Are you open?"},
            )

        assert response.status_code == 200
        # Verify orchestrator was called with the demo business ID
        call_kwargs = mock_handle.call_args
        event = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("event")
        assert event.business_id == DEMO_BUSINESS_ID

    @pytest.mark.asyncio
    async def test_simulate_requires_message_field(self, client: AsyncClient) -> None:
        """Request without 'message' field should return 422 Unprocessable Entity."""
        response = await client.post(
            "/api/v1/events/simulate",
            json={"from_number": "+15550001111"},  # missing 'message'
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_simulate_accepts_custom_business_id(self, client: AsyncClient) -> None:
        """Caller can override the business_id."""
        with patch(
            "backend.src.agents.orchestrator.Orchestrator.handle", new_callable=AsyncMock
        ) as mock_handle:
            mock_handle.return_value = {
                "agent": "after_hours",
                "reply": "Hello!",
                "outcome": "faq_answered",
                "tool_calls": [],
                "routing": {},
            }

            response = await client.post(
                "/api/v1/events/simulate",
                json={
                    "message": "Hi",
                    "business_id": "custom-biz-123",
                },
            )

        assert response.status_code == 200
        event = mock_handle.call_args.args[0]
        assert event.business_id == "custom-biz-123"


class TestInboundWebhook:
    """Tests for the Twilio-style /inbound webhook."""

    @pytest.mark.asyncio
    async def test_inbound_webhook_returns_200_immediately(self, client: AsyncClient) -> None:
        """
        The /inbound endpoint must return 200 right away (before background processing).
        Carriers will retry if they don't get a quick 200.
        """
        with patch("backend.src.agents.orchestrator.Orchestrator.handle", new_callable=AsyncMock):
            response = await client.post(
                "/api/v1/events/inbound",
                data={
                    "From": "+15550001234",
                    "To": "+15557654321",
                    "Body": "I need to book an appointment",
                    "MessageSid": "SM1234567890abcdef",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert "event_id" in data

    @pytest.mark.asyncio
    async def test_inbound_webhook_no_reply_field(self, client: AsyncClient) -> None:
        """The /inbound response should NOT include agent_reply (it's async)."""
        with patch("backend.src.agents.orchestrator.Orchestrator.handle", new_callable=AsyncMock):
            response = await client.post(
                "/api/v1/events/inbound",
                data={"From": "+15550001234", "To": "+15557654321", "Body": "hello"},
            )

        data = response.json()
        # The webhook response has no agent_reply — it's processed in background
        assert data.get("agent_reply") is None


class TestHealthEndpoints:

    @pytest.mark.asyncio
    async def test_healthz(self, client: AsyncClient) -> None:
        response = await client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_readyz(self, client: AsyncClient) -> None:
        response = await client.get("/readyz")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert "model" in data
