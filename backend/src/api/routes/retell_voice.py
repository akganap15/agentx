"""
Retell AI voice routes.

Two endpoints:
  POST /api/v1/voice/retell/register-call
    - Frontend calls this to start a Retell web call.
    - Backend calls Retell's REST API and returns an access_token to the browser.

  WS /api/v1/voice/retell/llm-webhook/{call_id}
    - Retell connects here via WebSocket for every call (custom LLM pattern).
    - Backend runs the existing Claude Orchestrator and sends replies back.

Setup (one-time, manual):
  1. Sign up at app.retellai.com
  2. Create a Custom LLM — set the WebSocket URL to:
       wss://<your-public-url>/api/v1/voice/retell/llm-webhook
  3. Create an Agent using that Custom LLM
  4. Copy the Agent ID → RETELL_AGENT_ID in .env
  5. Copy the API key  → RETELL_API_KEY in .env
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
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


@router.websocket("/llm-webhook/{call_id}")
async def llm_websocket(websocket: WebSocket, call_id: str):
    """
    Retell connects here via WebSocket for the duration of each call.

    Messages received from Retell:
      {
        "interaction_type": "response_required" | "reminder_required" | "update_only",
        "response_id": <int>,
        "transcript": [{"role": "agent"|"user", "content": "..."}]
      }

    Messages sent back to Retell:
      {
        "response_id": <int>,
        "content": "<agent reply>",
        "content_complete": true,
        "end_call": false
      }
    """
    await websocket.accept()
    logger.info("Retell WebSocket connected: call_id=%s", call_id)

    # Ensure session exists (may have been created by register-call)
    if call_id not in _retell_sessions:
        _retell_sessions[call_id] = {
            "history": [],
            "turns": 0,
            "business_id": settings.DEMO_BUSINESS_ID,
        }

    store = getattr(websocket.app.state, "store", None)
    session = _retell_sessions[call_id]

    try:
        while True:
            raw = await websocket.receive_text()
            body: Dict[str, Any] = json.loads(raw)

            interaction_type: str = body.get("interaction_type", "")
            response_id: int = body.get("response_id", 0)
            transcript: List[Dict[str, str]] = body.get("transcript", [])

            logger.info(
                "Retell WS message: call_id=%s interaction_type=%s turns=%d",
                call_id, interaction_type, len(transcript),
            )

            # update_only — no reply needed
            if interaction_type == "update_only":
                continue

            # Convert Retell transcript to Orchestrator history format
            history = [
                {
                    "role": "assistant" if t["role"] == "agent" else "customer",
                    "content": t["content"],
                }
                for t in transcript
            ]

            # Extract last user message
            user_messages = [t for t in transcript if t["role"] == "user"]
            last_user_msg = user_messages[-1]["content"] if user_messages else ""

            # No user message yet — send opening greeting
            if not last_user_msg:
                await websocket.send_text(json.dumps({
                    "response_id": response_id,
                    "content": f"Hi! Thanks for calling {settings.BUSINESS_NAME}. How can I help you today?",
                    "content_complete": True,
                    "end_call": False,
                }))
                continue

            # Run through existing Claude Orchestrator
            try:
                event = InboundEvent(
                    source=EventSource.VOICE,
                    event_type=EventType.SMS_INBOUND,
                    from_number="retell-web-call",
                    to_number=settings.TWILIO_PHONE_NUMBER or "unknown",
                    message_body=last_user_msg,
                    business_id=session["business_id"],
                )

                orchestrator = Orchestrator(store=store)
                result = await orchestrator.handle(event, history=history[:-1])
                agent_reply: str = result.get("reply", "") or "Let me look into that for you."
                outcome: str = result.get("outcome", "")

                session["turns"] += 1
                session["history"] = history

                end_call = outcome in ("appointment_booked", "callback_scheduled")

                logger.info(
                    "Retell reply: call_id=%s outcome=%s end_call=%s len=%d",
                    call_id, outcome, end_call, len(agent_reply),
                )

                await websocket.send_text(json.dumps({
                    "response_id": response_id,
                    "content": agent_reply,
                    "content_complete": True,
                    "end_call": end_call,
                }))

                if end_call:
                    break

            except Exception as exc:
                logger.exception("Orchestrator error: call_id=%s %s", call_id, exc)
                await websocket.send_text(json.dumps({
                    "response_id": response_id,
                    "content": "I'm sorry, I ran into a technical issue. Please try again.",
                    "content_complete": True,
                    "end_call": False,
                }))

    except WebSocketDisconnect:
        logger.info("Retell WebSocket disconnected: call_id=%s", call_id)
    except Exception as exc:
        logger.exception("Retell WebSocket error: call_id=%s %s", call_id, exc)
    finally:
        _retell_sessions.pop(call_id, None)
