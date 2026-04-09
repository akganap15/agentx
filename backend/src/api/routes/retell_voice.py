"""
Retell AI voice routes.

Two endpoints:
  POST /api/v1/voice/retell/register-call
    - Frontend calls this to start a Retell web call.
    - Backend calls Retell's REST API and returns an access_token to the browser.

  POST /api/v1/voice/retell/llm-webhook
    - Retell calls this for every conversation turn (custom LLM pattern).
    - Backend runs the existing Claude Orchestrator and returns the reply.

Setup (one-time, manual):
  1. Sign up at app.retellai.com
  2. Create a Custom LLM agent — set the LLM webhook URL to:
       POST https://<your-public-url>/api/v1/voice/retell/llm-webhook
  3. Copy the Agent ID → RETELL_AGENT_ID in .env
  4. Copy the API key  → RETELL_API_KEY in .env
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.src.agents.orchestrator import Orchestrator
from backend.src.config import settings
from backend.src.models.event import EventSource, EventType, InboundEvent

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory session store keyed by Retell call_id
_retell_sessions: Dict[str, Dict[str, Any]] = {}


@router.post(
    "/register-call",
    summary="Create a Retell web call and return an access token to the browser",
)
async def register_call() -> JSONResponse:
    """
    Called by the frontend when the user clicks 'Simulate Call' in Retell mode.
    Calls Retell's REST API to create a web call and returns the access_token.
    """
    if not settings.RETELL_API_KEY or not settings.RETELL_AGENT_ID:
        return JSONResponse(
            {"error": "RETELL_API_KEY and RETELL_AGENT_ID must be set in .env"},
            status_code=503,
        )

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.retellai.com/v2/create-web-call",
            headers={
                "Authorization": f"Bearer {settings.RETELL_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"agent_id": settings.RETELL_AGENT_ID},
            timeout=10.0,
        )

    if resp.status_code != 201:
        logger.error("Retell register-call failed: %s %s", resp.status_code, resp.text)
        return JSONResponse(
            {"error": f"Retell API error: {resp.status_code}", "detail": resp.text},
            status_code=502,
        )

    data = resp.json()
    access_token = data.get("access_token")
    call_id = data.get("call_id")

    logger.info("Retell web call created: call_id=%s", call_id)

    _retell_sessions[call_id] = {
        "history": [],
        "turns": 0,
        "business_id": settings.DEMO_BUSINESS_ID,
    }

    return JSONResponse({"access_token": access_token, "call_id": call_id})


@router.post(
    "/llm-webhook",
    summary="Retell custom LLM webhook — processes each conversation turn with Claude",
)
async def llm_webhook(request: Request) -> JSONResponse:
    """
    Retell calls this endpoint for every conversation turn.

    Request body from Retell:
      {
        "interaction_type": "response_required" | "reminder_required" | "update_only",
        "response_id": <int>,
        "transcript": [{"role": "agent"|"user", "content": "..."}],
        "call": {"call_id": "...", "metadata": {}}
      }

    Expected response:
      {
        "response_id": <int>,
        "content": "<agent reply>",
        "content_complete": true,
        "end_call": false
      }
    """
    body = await request.json()

    interaction_type: str = body.get("interaction_type", "")
    response_id: int = body.get("response_id", 0)
    transcript: List[Dict[str, str]] = body.get("transcript", [])
    call_info: Dict[str, Any] = body.get("call", {})
    call_id: str = call_info.get("call_id", "unknown")

    logger.info(
        "Retell webhook: call_id=%s interaction_type=%s turns=%d",
        call_id, interaction_type, len(transcript),
    )

    # For update_only just acknowledge — no response needed
    if interaction_type == "update_only":
        return JSONResponse({"response_id": response_id, "content": "", "content_complete": False})

    # Ensure session exists
    if call_id not in _retell_sessions:
        _retell_sessions[call_id] = {
            "history": [],
            "turns": 0,
            "business_id": settings.DEMO_BUSINESS_ID,
        }

    session = _retell_sessions[call_id]

    # Convert Retell transcript to the history format used by Orchestrator
    # Retell roles: "agent" / "user"  →  Orchestrator: "assistant" / "customer"
    history = [
        {
            "role": "assistant" if t["role"] == "agent" else "customer",
            "content": t["content"],
        }
        for t in transcript
    ]

    # Get the last user message as the event body
    user_messages = [t for t in transcript if t["role"] == "user"]
    last_user_msg = user_messages[-1]["content"] if user_messages else ""

    if not last_user_msg:
        return JSONResponse({
            "response_id": response_id,
            "content": "I'm here — go ahead, how can I help?",
            "content_complete": True,
            "end_call": False,
        })

    # Build an InboundEvent and run through the existing Orchestrator
    store = None  # Orchestrator will use in-memory store via app.state if needed
    event = InboundEvent(
        source=EventSource.VOICE,
        event_type=EventType.SMS_INBOUND,
        from_number="retell-web-call",
        to_number=settings.TWILIO_PHONE_NUMBER or "unknown",
        message_body=last_user_msg,
        business_id=session["business_id"],
    )

    try:
        orchestrator = Orchestrator(store=store)
        result = await orchestrator.handle(event, history=history[:-1])  # exclude last user turn
        agent_reply: str = result.get("reply", "")
        outcome: str = result.get("outcome", "")

        if not agent_reply:
            agent_reply = "Let me look into that for you."

        session["turns"] += 1
        session["history"] = history

        # End the call when a concrete outcome is reached
        end_call = outcome in ("appointment_booked", "callback_scheduled")

        logger.info(
            "Retell reply: call_id=%s outcome=%s end_call=%s reply_len=%d",
            call_id, outcome, end_call, len(agent_reply),
        )

        if end_call:
            _retell_sessions.pop(call_id, None)

        return JSONResponse({
            "response_id": response_id,
            "content": agent_reply,
            "content_complete": True,
            "end_call": end_call,
        })

    except Exception as exc:
        logger.exception("Orchestrator failed for Retell call_id=%s: %s", call_id, exc)
        return JSONResponse({
            "response_id": response_id,
            "content": (
                "I'm sorry, I ran into a technical issue. "
                "Please try again or call us directly."
            ),
            "content_complete": True,
            "end_call": False,
        })
