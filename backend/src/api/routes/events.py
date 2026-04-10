"""
Inbound event webhook gateway.

POST /api/v1/events/inbound
  - Validates the payload (Twilio / T-Mobile webhook signature + schema)
  - Constructs an InboundEvent
  - Dispatches to the Orchestrator agent asynchronously
  - Returns 200 immediately so the carrier doesn't retry

POST /api/v1/events/simulate
  - Convenience endpoint for hackathon demos — no signature validation
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from backend.src.agents.orchestrator import Orchestrator
from backend.src.config import settings
from backend.src.models.conversation import Conversation, ConversationMessage, MessageRole
from backend.src.models.event import EventSource, EventType, InboundEvent

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory conversation history store keyed by conversation_id
_conversation_history: Dict[str, List[Dict[str, Any]]] = {}


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class TwilioWebhookPayload(BaseModel):
    """Twilio SMS webhook — field names match Twilio's POST body."""

    From: str = Field(..., description="Sender phone number in E.164 format.")
    To: str = Field(..., description="Recipient phone number (our Twilio number).")
    Body: str = Field(..., description="SMS message body.")
    MessageSid: Optional[str] = None
    AccountSid: Optional[str] = None
    NumMedia: Optional[str] = "0"


class SimulateEventPayload(BaseModel):
    """Payload for the /simulate endpoint used in demos and tests."""

    from_number: str = Field(default="+15005550006", description="Sender phone number.")
    to_number: str = Field(default="+15005550001", description="Destination number.")
    message: str = Field(..., description="The inbound message text.")
    business_id: Optional[str] = Field(
        default=None, description="Target business ID; defaults to DEMO_BUSINESS_ID."
    )
    source: EventSource = EventSource.SMS
    conversation_id: Optional[str] = Field(
        default=None, description="Pass to continue an existing conversation."
    )


class EventResponse(BaseModel):
    status: str
    event_id: str
    agent_reply: Optional[str] = None
    agent_used: Optional[str] = None
    conversation_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Webhook signature validation helper
# ---------------------------------------------------------------------------

def _validate_twilio_signature(
    request_url: str,
    params: dict,
    x_twilio_signature: str,
    auth_token: str,
) -> bool:
    """
    Verify that the request genuinely came from Twilio.
    See: https://www.twilio.com/docs/usage/webhooks/webhooks-security
    """
    sorted_params = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    signature_base = request_url + sorted_params
    expected = hmac.new(
        auth_token.encode("utf-8"),
        signature_base.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    import base64
    expected_b64 = base64.b64encode(expected).decode("utf-8")
    return hmac.compare_digest(expected_b64, x_twilio_signature)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/inbound",
    status_code=status.HTTP_200_OK,
    summary="Receive inbound SMS / voice events from Twilio or T-Mobile",
)
async def inbound_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_twilio_signature: Optional[str] = Header(default=None),
) -> EventResponse:
    """
    Webhook called by Twilio (or T-Mobile gateway) on every inbound SMS.

    Steps:
      1. Parse form data from carrier
      2. Validate HMAC signature when in production
      3. Map to InboundEvent model
      4. Dispatch to Orchestrator in background (fire-and-forget so we return 200 fast)
    """
    form_data = await request.form()
    payload_dict = dict(form_data)

    # In production validate the Twilio signature
    if settings.is_production:
        if not x_twilio_signature:
            raise HTTPException(status_code=403, detail="Missing X-Twilio-Signature header.")
        valid = _validate_twilio_signature(
            str(request.url),
            payload_dict,
            x_twilio_signature,
            settings.TWILIO_AUTH_TOKEN,
        )
        if not valid:
            raise HTTPException(status_code=403, detail="Invalid Twilio signature.")

    payload = TwilioWebhookPayload(**payload_dict)

    # Resolve business_id from the destination number (look up in store)
    store = getattr(request.app.state, "store", None)
    business_id = settings.DEMO_BUSINESS_ID  # fallback for demo

    event = InboundEvent(
        source=EventSource.SMS,
        event_type=EventType.SMS_INBOUND,
        from_number=payload.From,
        to_number=payload.To,
        message_body=payload.Body,
        business_id=business_id,
        raw_payload=payload_dict,
    )

    logger.info("Inbound SMS event=%s from=%s business=%s", event.id, event.from_number, event.business_id)

    orchestrator = Orchestrator(store=store)
    background_tasks.add_task(_dispatch, orchestrator, event)

    return EventResponse(status="accepted", event_id=event.id)


async def _dispatch(orchestrator: Orchestrator, event: InboundEvent) -> None:
    """Background task: runs the orchestrator and sends reply via SMS tool."""
    try:
        result = await orchestrator.handle(event)
        logger.info("Orchestrator result for event=%s: agent=%s", event.id, result.get("agent"))
    except Exception as exc:
        logger.exception("Orchestrator failed for event=%s: %s", event.id, exc)


@router.post(
    "/simulate",
    status_code=status.HTTP_200_OK,
    summary="Simulate an inbound event (demo / testing only — no signature check)",
)
async def simulate_event(
    payload: SimulateEventPayload,
    request: Request,
) -> EventResponse:
    """
    Convenience endpoint for hackathon demos.
    Runs the full agent pipeline synchronously and returns the reply.
    """
    store = getattr(request.app.state, "store", None)
    business_id = payload.business_id or settings.DEMO_BUSINESS_ID

    event = InboundEvent(
        source=payload.source,
        event_type=EventType.SMS_INBOUND,
        from_number=payload.from_number,
        to_number=payload.to_number,
        message_body=payload.message,
        business_id=business_id,
    )

    conv_id = payload.conversation_id or event.id

    # Build message history for multi-turn conversations
    history = _conversation_history.get(conv_id, [])
    history.append({"role": "customer", "content": payload.message, "ts": event.id})

    orchestrator = Orchestrator(store=store)
    result = await orchestrator.handle(event, history=history)

    agent_reply = result.get("reply", "")
    agent_used = result.get("agent", "orchestrator")

    history.append({"role": "agent", "content": agent_reply, "agent": agent_used, "ts": event.id})
    _conversation_history[conv_id] = history[-20:]  # keep last 20 turns

    # Persist conversation to the store so the dashboard reflects it
    if store:
        try:
            existing = await store.get_conversation(conv_id)
            if existing:
                existing.messages.append(
                    ConversationMessage(role=MessageRole.USER, content=payload.message)
                )
                existing.messages.append(
                    ConversationMessage(role=MessageRole.ASSISTANT, content=agent_reply)
                )
                existing.last_message = agent_reply
                existing.agent = agent_used
                await store.save_conversation(existing)
            else:
                conv = Conversation(
                    id=conv_id,
                    business_id=business_id,
                    customer_phone=payload.from_number,
                    agent=agent_used,
                    last_message=agent_reply,
                    messages=[
                        ConversationMessage(role=MessageRole.USER, content=payload.message),
                        ConversationMessage(role=MessageRole.ASSISTANT, content=agent_reply),
                    ],
                )
                await store.save_conversation(conv)
        except Exception as exc:
            logger.warning("Failed to persist conversation %s: %s", conv_id, exc)

    return EventResponse(
        status="processed",
        event_id=event.id,
        agent_reply=agent_reply,
        agent_used=agent_used,
        conversation_id=conv_id,
    )
