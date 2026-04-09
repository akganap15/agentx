"""
Inbound event model.

An InboundEvent is the normalised representation of any incoming trigger —
an SMS message, a voice call, a review notification, or a scheduled job.
The Orchestrator consumes InboundEvents and routes them to specialist agents.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class EventSource(str, Enum):
    SMS = "sms"
    VOICE = "voice"
    EMAIL = "email"
    REVIEW = "review"
    SCHEDULED = "scheduled"
    WEBHOOK = "webhook"


class EventType(str, Enum):
    SMS_INBOUND = "sms_inbound"
    VOICE_INBOUND = "voice_inbound"
    REVIEW_NEW = "review_new"
    APPOINTMENT_NOSHOW = "appointment_noshow"
    CAMPAIGN_TRIGGER = "campaign_trigger"
    AFTER_HOURS = "after_hours"


class InboundEvent(BaseModel):
    """
    Normalised inbound event consumed by the Orchestrator.

    All external triggers (Twilio webhook, scheduled job, review alert)
    are converted to this shape before hitting the agent pipeline.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=datetime.utcnow)

    source: EventSource
    event_type: EventType

    # Communication fields (populated for SMS/voice events)
    from_number: Optional[str] = Field(default=None, description="Caller/sender phone in E.164.")
    to_number: Optional[str] = Field(default=None, description="Destination number.")
    message_body: Optional[str] = Field(default=None, description="The raw message text.")

    # Business context
    business_id: str = Field(..., description="Which business this event belongs to.")

    # Extra metadata (original webhook payload, review URL, etc.)
    raw_payload: Optional[Dict[str, Any]] = Field(default=None, exclude=True)

    class Config:
        use_enum_values = True
