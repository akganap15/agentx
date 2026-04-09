"""
Pytest fixtures shared across all test modules.

Provides:
  - app: FastAPI test application instance
  - client: httpx AsyncClient for making HTTP requests
  - store: pre-seeded InMemoryStore
  - demo_business: Pete's Plumbing Business model
  - demo_event_factory: factory for creating InboundEvent instances
"""

from __future__ import annotations

import os
from typing import AsyncGenerator, Callable

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

# Ensure we use the in-memory store and a fake Anthropic key during tests
os.environ.setdefault("USE_IN_MEMORY_STORE", "true")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-key-for-testing-only")
os.environ.setdefault("ANTHROPIC_MODEL", "claude-sonnet-4-6")

from backend.server import app as fastapi_app
from backend.src.db.store import InMemoryStore, demo_store, DEMO_BUSINESS_ID
from backend.src.models.business import Business
from backend.src.models.event import EventSource, EventType, InboundEvent


# ---------------------------------------------------------------------------
# App and HTTP client
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def app() -> FastAPI:
    """Return the FastAPI app instance."""
    fastapi_app.state.store = demo_store
    return fastapi_app


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP test client."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

@pytest.fixture
def store() -> InMemoryStore:
    """Return the pre-seeded demo store."""
    return demo_store


@pytest_asyncio.fixture
async def demo_business(store: InMemoryStore) -> Business:
    """Return the pre-loaded Pete's Plumbing business."""
    business = await store.get_business(DEMO_BUSINESS_ID)
    assert business is not None, "Demo business not found in store"
    return business


# ---------------------------------------------------------------------------
# Event factory
# ---------------------------------------------------------------------------

@pytest.fixture
def make_event() -> Callable[..., InboundEvent]:
    """Factory fixture for creating InboundEvent instances."""
    def _factory(
        message: str = "Hi, I need a plumber",
        from_number: str = "+15550009999",
        business_id: str = DEMO_BUSINESS_ID,
        source: EventSource = EventSource.SMS,
        event_type: EventType = EventType.SMS_INBOUND,
        raw_payload: dict | None = None,
    ) -> InboundEvent:
        return InboundEvent(
            source=source,
            event_type=event_type,
            from_number=from_number,
            to_number="+15557654321",
            message_body=message,
            business_id=business_id,
            raw_payload=raw_payload,
        )
    return _factory


# ---------------------------------------------------------------------------
# Anthropic mock (prevents real API calls in unit tests)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Replace the Anthropic client with a mock that returns predictable responses.
    This prevents real API calls during unit tests while still exercising
    the agent loop logic.
    """
    import anthropic
    from unittest.mock import MagicMock, patch

    # Build a fake message response
    def _make_fake_response(stop_reason: str = "end_turn", text: str = "Test response from mock agent."):
        block = MagicMock()
        block.type = "text"
        block.text = text
        response = MagicMock()
        response.content = [block]
        response.stop_reason = stop_reason
        return response

    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_fake_response()

    with patch.object(anthropic, "Anthropic", return_value=mock_client):
        yield
