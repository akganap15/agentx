"""
Voice webhook routes — handles inbound calls via Twilio.

Flow:
  1. Customer calls the business number
  2. T-Mobile conditional forward sends unanswered calls to the Twilio number
  3. Twilio POSTs to /api/v1/voice/inbound  → greet + start listening
  4. Caller speaks → Twilio transcribes → POSTs to /api/v1/voice/respond
  5. Backend runs through orchestrator → Claude agent generates reply
  6. TwiML <Say> speaks the reply back → gather next input
  7. Loop until resolved or max turns reached → schedule callback

Setup:
  - Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER in .env
  - Set VOICE_WEBHOOK_BASE_URL to your public URL (ngrok or deployed)
  - In Twilio console: Voice → Phone Number → Webhook = POST {base_url}/api/v1/voice/inbound
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from datetime import datetime

import asyncio
import urllib.request
import json as _json
import uuid

import httpx
import websockets
from fastapi import APIRouter, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from backend.src.agents.orchestrator import Orchestrator
from backend.src.config import settings
from backend.src.models.event import EventSource, EventType, InboundEvent

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory voice session store keyed by Twilio CallSid
# Each session: { history: [], turns: int, business_id: str }
_voice_sessions: Dict[str, Dict[str, Any]] = {}

MAX_TURNS = 8  # end call after this many exchanges
BUSINESS_NAME = "Andy Plumbing"
VOICE = "Polly.Joanna"  # AWS Polly via Twilio — natural female voice


def _twiml(xml_body: str) -> Response:
    return Response(content=f"<?xml version='1.0' encoding='UTF-8'?><Response>{xml_body}</Response>",
                    media_type="application/xml")


def _clean_for_speech(text: str) -> str:
    import re
    # Strip emojis
    text = re.sub(r'[^\x00-\x7F]+', '', text)
    # Strip markdown bold/italic
    text = re.sub(r'\*+', '', text)
    # Strip markdown headers
    text = re.sub(r'#+\s*', '', text)
    # Strip table rows and horizontal rules
    text = re.sub(r'\|[^\n]*', '', text)
    text = re.sub(r'-{3,}', '', text)
    # Strip numbered list markers like "1." at start of word
    text = re.sub(r'\d+\.\s', '', text)
    # Collapse whitespace
    text = re.sub(r'\n+', ' ', text)
    text = re.sub(r'\s{2,}', ' ', text).strip()
    # Escape XML special chars
    text = text.replace("&", "and").replace("<", "").replace(">", "").replace('"', "'")
    return text


def _say(text: str, voice: str = VOICE) -> str:
    text = _clean_for_speech(text)
    return f'<Say voice="{voice}">{text}</Say>'


def _gather(action: str, say_text: str) -> str:
    safe = _say(say_text)
    return (
        f'<Gather input="speech" action="{action}" method="POST" '
        f'timeout="6" speechTimeout="2" language="en-US">'
        f'{safe}'
        f'</Gather>'
        # If no input detected, re-ask once then hangup
        f'{_say("Sorry, I didn\'t catch that. Could you please repeat?")}'
        f'<Gather input="speech" action="{action}" method="POST" '
        f'timeout="6" speechTimeout="2" language="en-US"></Gather>'
        f'{_say("I didn\'t hear anything. I\'ll have someone call you back shortly. Goodbye!")}'
        f'<Hangup/>'
    )


VOICE_SYSTEM_PROMPT = """You are a friendly and professional AI voice assistant for Andy Plumbing, a residential plumbing service in Austin, TX.

Your job: help callers book appointments or get help with plumbing issues.

Appointment booking — collect ALL of these before confirming:
1. Customer's full name
2. Service address (street, city)
3. Description of the problem
4. Preferred date AND time (offer morning 8am-12pm or afternoon 12pm-5pm)
5. Best callback number

Do NOT confirm the booking or say goodbye until you have all 5 items. If the caller skips one, ask for it.

After collecting everything, read back a full summary and ask the caller to confirm before ending.

Emergency calls: immediately tell them to shut off the main water valve, get their address, and assure a tech within 2 hours.

Voice rules:
- 1-2 short sentences per turn
- Ask exactly ONE question per turn, never two at once
- Be warm, calm, and efficient"""

# In-memory chat history for voice conversations
_chat_sessions: dict[str, list] = {}


@router.websocket("/ws")
async def realtime_ws_relay(ws: WebSocket):
    """
    WebSocket relay: browser ↔ backend ↔ LiteLLM Realtime API.
    Keeps the API key server-side. Proxies all JSON events bidirectionally.
    """
    await ws.accept()

    litellm_key = getattr(settings, "LITELLM_API_KEY", "")
    litellm_url = getattr(settings, "LITELLM_BASE_URL", "https://llm.t-mobile.com")

    # Realtime API always uses the realtime-preview model regardless of LITELLM_MODEL
    realtime_model = "gpt-4o-mini-realtime-preview"

    # Build the upstream WebSocket URL
    wss_url = f"{litellm_url.rstrip('/').replace('http://', 'ws://').replace('https://', 'wss://')}/v1/realtime?model={realtime_model}"

    logger.info("Connecting to Realtime relay: %s", wss_url)

    try:
        async with websockets.connect(
            wss_url,
            additional_headers={
                "Authorization": f"Bearer {litellm_key}",
                "OpenAI-Beta": "realtime=v1",
            },
        ) as upstream:

            relay_stats = {
                "speech_stopped_at": None,
                "first_audio_at": None,
                "turn": 0,
            }

            async def browser_to_upstream():
                """Forward messages from browser → LiteLLM, log key timing events."""
                try:
                    while True:
                        msg = await ws.receive_text()
                        await upstream.send(msg)
                        # Log speech_stopped so we can correlate with first audio
                        try:
                            evt = _json.loads(msg)
                            if evt.get("type") == "input_audio_buffer.speech_stopped":
                                relay_stats["speech_stopped_at"] = asyncio.get_event_loop().time()
                                relay_stats["first_audio_at"] = None
                                relay_stats["turn"] += 1
                                logger.info("[Relay] Turn %d: speech_stopped", relay_stats["turn"])
                        except Exception:
                            pass
                except (WebSocketDisconnect, Exception):
                    pass

            async def upstream_to_browser():
                """Forward messages from LiteLLM → browser, log first audio timing."""
                try:
                    async for msg in upstream:
                        text = msg if isinstance(msg, str) else msg.decode()
                        await ws.send_text(text)
                        # Log TTFA on first audio delta
                        try:
                            evt = _json.loads(text)
                            if evt.get("type") == "response.audio.delta" and relay_stats["first_audio_at"] is None:
                                relay_stats["first_audio_at"] = asyncio.get_event_loop().time()
                                if relay_stats["speech_stopped_at"]:
                                    ttfa_ms = int((relay_stats["first_audio_at"] - relay_stats["speech_stopped_at"]) * 1000)
                                    logger.info("[Relay] Turn %d TTFA (server-side): %dms", relay_stats["turn"], ttfa_ms)
                            elif evt.get("type") == "response.done" and relay_stats["speech_stopped_at"]:
                                turn_ms = int((asyncio.get_event_loop().time() - relay_stats["speech_stopped_at"]) * 1000)
                                logger.info("[Relay] Turn %d total: %dms", relay_stats["turn"], turn_ms)
                        except Exception:
                            pass
                except Exception:
                    pass

            # Run both directions concurrently until one closes
            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(browser_to_upstream()),
                    asyncio.create_task(upstream_to_browser()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

    except Exception as exc:
        logger.error("Realtime relay error: %s", exc)
        try:
            await ws.send_text(_json.dumps({"type": "error", "error": {"message": str(exc)}}))
        except Exception:
            pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass


@router.post(
    "/chat",
    summary="LiteLLM-backed voice chat — takes a transcript, returns an AI reply",
)
async def voice_chat(request: Request) -> JSONResponse:
    """
    Called by VoiceCall.jsx after the user speaks.
    Maintains per-session conversation history for multi-turn context.
    """
    body = await request.json()
    message      = (body.get("message") or "").strip()
    session_id   = body.get("session_id") or str(uuid.uuid4())

    if not message:
        return JSONResponse({"error": "empty message"}, status_code=400)

    # Build or retrieve history
    history = _chat_sessions.setdefault(session_id, [])
    history.append({"role": "user", "content": message})

    messages = [{"role": "system", "content": VOICE_SYSTEM_PROMPT}] + history

    litellm_key  = getattr(settings, "LITELLM_API_KEY", "")
    litellm_url  = getattr(settings, "LITELLM_BASE_URL", "https://llm.t-mobile.com")
    litellm_model = getattr(settings, "LITELLM_MODEL", "gpt-4o-mini")

    # Try /v1/chat/completions first, fall back to /chat/completions
    endpoint = f"{litellm_url.rstrip('/')}/v1/chat/completions"

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {litellm_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": litellm_model,
                    "messages": messages,
                    "max_tokens": 120,
                    "temperature": 0.7,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        reply = data["choices"][0]["message"]["content"].strip()
        history.append({"role": "assistant", "content": reply})

        # Cap history at last 20 turns to avoid token bloat
        if len(history) > 40:
            _chat_sessions[session_id] = history[-40:]

        return JSONResponse({"reply": reply, "session_id": session_id})

    except httpx.HTTPStatusError as exc:
        logger.error("LiteLLM error %s: %s", exc.response.status_code, exc.response.text)
        return JSONResponse({"error": f"LiteLLM {exc.response.status_code}"}, status_code=502)
    except Exception as exc:
        logger.exception("voice_chat failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post(
    "/realtime-session",
    summary="Create an ephemeral OpenAI Realtime session token for the browser",
)
async def realtime_session() -> JSONResponse:
    """
    Exchange the server-side OPENAI_API_KEY for a short-lived ephemeral token.
    The browser uses this token to connect directly to the OpenAI Realtime API
    via WebRTC — the real API key never leaves the server.
    """
    openai_key = getattr(settings, "OPENAI_API_KEY", None)
    if not openai_key:
        return JSONResponse({"error": "OPENAI_API_KEY not configured"}, status_code=500)

    payload = _json.dumps({
        "model": "gpt-4o-realtime-preview-2024-12-17",
        "voice": "shimmer",
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/realtime/sessions",
        data=payload,
        headers={
            "Authorization": f"Bearer {openai_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = _json.loads(r.read())
        return JSONResponse(data)
    except Exception as exc:
        logger.exception("Failed to create OpenAI Realtime session: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post(
    "/inbound",
    summary="Twilio inbound call webhook — answers the call and starts conversation",
)
async def inbound_call(
    request: Request,
    CallSid: str = Form(...),
    From: str = Form(...),
    To: str = Form(...),
    CallStatus: Optional[str] = Form(default=None),
) -> Response:
    """
    Called by Twilio when a call arrives.
    Greets the caller and starts speech gathering.
    """
    logger.info("Inbound call: CallSid=%s From=%s To=%s", CallSid, From, To)

    # Initialise session
    _voice_sessions[CallSid] = {
        "history": [],
        "turns": 0,
        "from_number": From,
        "business_id": settings.DEMO_BUSINESS_ID,
        "started_at": datetime.utcnow().isoformat(),
    }

    respond_url = f"{settings.VOICE_WEBHOOK_BASE_URL}/api/v1/voice/respond"
    greeting = (
        f"Hi! Thanks for calling {BUSINESS_NAME}. "
        f"I'm an AI assistant and I'm here to help. "
        f"How can I help you today?"
    )

    return _twiml(_gather(action=respond_url, say_text=greeting))


@router.post(
    "/respond",
    summary="Twilio gather callback — processes speech and replies",
)
async def respond_to_caller(
    request: Request,
    CallSid: str = Form(...),
    From: str = Form(default=""),
    SpeechResult: Optional[str] = Form(default=None),
    Confidence: Optional[str] = Form(default=None),
) -> Response:
    """
    Called by Twilio after the caller speaks.
    Runs the transcript through the agent orchestrator and speaks the reply.
    """
    session = _voice_sessions.get(CallSid)
    if not session:
        # Session expired or missed inbound — restart gracefully
        logger.warning("No session for CallSid=%s, starting fresh", CallSid)
        session = {
            "history": [],
            "turns": 0,
            "from_number": From,
            "business_id": settings.DEMO_BUSINESS_ID,
            "started_at": datetime.utcnow().isoformat(),
        }
        _voice_sessions[CallSid] = session

    caller_said = (SpeechResult or "").strip()
    logger.info("CallSid=%s turn=%d speech=%r confidence=%s",
                CallSid, session["turns"], caller_said[:80], Confidence)

    # End call if max turns reached
    if session["turns"] >= MAX_TURNS:
        del _voice_sessions[CallSid]
        return _twiml(
            _say("I've noted everything down. Someone from the team will follow up with you shortly. "
                 "Thank you for calling and have a great day! Goodbye!")
            + "<Hangup/>"
        )

    # Handle empty speech
    if not caller_said:
        respond_url = f"{settings.VOICE_WEBHOOK_BASE_URL}/api/v1/voice/respond"
        return _twiml(_gather(respond_url, "I'm still here — go ahead, how can I help?"))

    # Add to history
    session["history"].append({
        "role": "customer",
        "content": caller_said,
        "ts": datetime.utcnow().isoformat(),
    })
    session["turns"] += 1

    # Run through orchestrator
    store = getattr(request.app.state, "store", None)
    event = InboundEvent(
        source=EventSource.VOICE,
        event_type=EventType.SMS_INBOUND,
        from_number=session["from_number"],
        to_number=settings.TWILIO_PHONE_NUMBER,
        message_body=caller_said,
        business_id=session["business_id"],
    )

    try:
        import asyncio
        orchestrator = Orchestrator(store=store)
        result = await asyncio.wait_for(
            orchestrator.handle(event, history=session["history"]),
            timeout=25.0,
        )
        agent_reply = result.get("reply", "")
        agent_used = result.get("agent", "orchestrator")

        if not agent_reply:
            agent_reply = "Let me look into that for you. Can you give me a moment?"

        logger.info("CallSid=%s agent=%s reply_len=%d", CallSid, agent_used, len(agent_reply))

    except Exception as exc:
        logger.exception("Orchestrator failed for CallSid=%s: %s", CallSid, exc)
        agent_reply = (
            "I'm sorry, I ran into a technical issue. "
            "Please leave your name and number and someone will call you right back."
        )

    # Add agent reply to history
    session["history"].append({
        "role": "agent",
        "content": agent_reply,
        "agent": agent_used if "agent_used" in dir() else "orchestrator",
        "ts": datetime.utcnow().isoformat(),
    })

    # Only end the call when something concrete is resolved
    outcome = result.get("outcome", "")
    conversation_done = outcome in ("appointment_booked", "callback_scheduled")

    respond_url = f"{settings.VOICE_WEBHOOK_BASE_URL}/api/v1/voice/respond"

    if conversation_done:
        closing = " We'll send you a confirmation shortly. Thank you for calling, goodbye!"
        _voice_sessions.pop(CallSid, None)
        return _twiml(_say(agent_reply + closing) + "<Hangup/>")

    return _twiml(_gather(action=respond_url, say_text=agent_reply))


@router.post(
    "/status",
    summary="Twilio call status callback — cleans up session on call end",
)
async def call_status(
    CallSid: str = Form(...),
    CallStatus: str = Form(...),
) -> Response:
    """Clean up session when call ends."""
    if CallStatus in ("completed", "failed", "busy", "no-answer", "canceled"):
        session = _voice_sessions.pop(CallSid, None)
        if session:
            logger.info(
                "Call ended: CallSid=%s status=%s turns=%d duration=%s",
                CallSid, CallStatus, session.get("turns", 0), session.get("started_at"),
            )
    return Response(content="", status_code=204)
