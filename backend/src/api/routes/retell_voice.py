"""
Retell AI voice routes.

Two endpoints:
  POST /api/v1/voice/retell/register-call
    - Frontend calls this to start a Retell web call.
    - Backend calls Retell's REST API and returns an access_token to the browser.

  WS /api/v1/voice/retell/llm-webhook
    - Retell opens a WebSocket for each call (custom LLM pattern).
    - Backend runs the existing Claude Orchestrator and returns the reply.

Setup (one-time, manual):
  1. Sign up at app.retellai.com
  2. Create a Custom LLM agent — set the Custom LLM URL to:
       wss://<your-public-url>/api/v1/voice/retell/llm-webhook
  3. Copy the Agent ID → RETELL_AGENT_ID in .env
  4. Copy the API key  → RETELL_API_KEY in .env
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from backend.src.agents.litellm_client import litellm_classify
from backend.src.config import settings
from backend.src.models.event import EventSource, EventType, InboundEvent

# Voice-tuned system prompt — short, conversational, no markdown
RETELL_SYSTEM_PROMPT = (
    "You are a friendly AI receptionist for Andy Plumbing in Austin, TX. "
    "You answer phone calls. CRITICAL VOICE RULES:\n"
    "- Reply in 1-2 SHORT sentences only. No markdown, no lists, no bullets.\n"
    "- Ask ONE question at a time.\n"
    "- Sound warm and conversational, like a real person.\n"
    "- For emergencies (burst pipe, flooding, water heater) tell them to shut off "
    "the main water valve first, then dispatch a technician.\n"
    "- Collect: name, address, phone, problem description, preferred time.\n"
    "- After collecting all info, confirm the booking and end the call.\n"
    "- If asked about hours: Mon-Fri 8am-6pm, Sat 9am-2pm, closed Sunday. "
    "Emergency service available 24/7."
)

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
async def llm_webhook(ws: WebSocket, call_id: str):
    """
    Retell Custom LLM WebSocket endpoint.

    Retell opens a WebSocket for each call. On each conversation turn it sends
    a JSON message and expects a JSON response back over the same connection.

    Inbound message from Retell:
      {
        "interaction_type": "response_required" | "reminder_required" | "update_only",
        "response_id": <int>,
        "transcript": [{"role": "agent"|"user", "content": "..."}]
      }

    Outbound response:
      {
        "response_id": <int>,
        "content": "<agent reply>",
        "content_complete": true,
        "end_call": false
      }
    """
    await ws.accept()
    logger.info("Retell WebSocket connected: call_id=%s", call_id)

    try:
        while True:
            raw = await ws.receive_text()
            body = json.loads(raw)

            interaction_type: str = body.get("interaction_type", "")
            response_id: int = body.get("response_id", 0)
            transcript: List[Dict[str, str]] = body.get("transcript", [])
            call_info: Dict[str, Any] = body.get("call", {})
            call_id = call_info.get("call_id", call_id)

            logger.info(
                "Retell WS message: call_id=%s interaction_type=%s turns=%d",
                call_id, interaction_type, len(transcript),
            )

            # For update_only just acknowledge — no response needed
            if interaction_type == "update_only":
                continue

            # Ensure session exists
            if call_id not in _retell_sessions:
                _retell_sessions[call_id] = {
                    "history": [],
                    "turns": 0,
                    "business_id": settings.DEMO_BUSINESS_ID,
                }

            session = _retell_sessions[call_id]

            # Build a chat history string from Retell's transcript
            # Format as a flat conversation for the LLM
            convo_lines = []
            for t in transcript:
                role = "Customer" if t["role"] == "user" else "Agent"
                convo_lines.append(f"{role}: {t['content']}")
            convo_text = "\n".join(convo_lines)

            # Get the last user message
            user_messages = [t for t in transcript if t["role"] == "user"]
            last_user_msg = user_messages[-1]["content"] if user_messages else ""

            if not last_user_msg:
                await ws.send_text(json.dumps({
                    "response_id": response_id,
                    "content": "I'm here — go ahead, how can I help?",
                    "content_complete": True,
                    "end_call": False,
                }))
                continue

            # Single fast LLM call — no orchestrator, no specialist routing
            try:
                user_prompt = (
                    f"Conversation so far:\n{convo_text}\n\n"
                    f"Reply as the agent in 1-2 short sentences. "
                    f"Do not include 'Agent:' prefix."
                )
                agent_reply = await litellm_classify(
                    system=RETELL_SYSTEM_PROMPT,
                    user_message=user_prompt,
                    max_tokens=120,
                )
                outcome = ""
            except Exception as exc:
                logger.exception("LLM call failed: call_id=%s %s", call_id, exc)
                agent_reply = "I'm sorry, I ran into a technical issue. Please try again."
                outcome = ""

            session["turns"] += 1
            end_call = False

            logger.info(
                "Retell reply: call_id=%s outcome=%s end_call=%s reply_len=%d",
                call_id, outcome, end_call, len(agent_reply),
            )

            # Try to send the reply. If Retell already closed the socket, log and exit cleanly.
            try:
                await ws.send_text(json.dumps({
                    "response_id": response_id,
                    "content": agent_reply,
                    "content_complete": True,
                    "end_call": end_call,
                }))
            except (WebSocketDisconnect, RuntimeError) as exc:
                logger.info(
                    "Retell closed socket before reply could be sent: call_id=%s (%s)",
                    call_id, type(exc).__name__,
                )
                return

            if end_call:
                _retell_sessions.pop(call_id, None)
                return

    except WebSocketDisconnect:
        logger.info("Retell WebSocket disconnected: call_id=%s", call_id)
    except Exception as exc:
        logger.exception("Retell WebSocket error: call_id=%s %s", call_id, exc)
    finally:
        _retell_sessions.pop(call_id, None)
        try:
            await ws.close()
        except Exception:
            pass
