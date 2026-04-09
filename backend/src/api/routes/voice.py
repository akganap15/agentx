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

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response

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
