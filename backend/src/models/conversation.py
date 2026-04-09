"""
Conversation and interaction models.

A Conversation is a thread of messages between an AI agent and a customer.
Each interaction event is stored as a ConversationMessage.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ConversationMessage(BaseModel):
    """A single turn in a conversation."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    role: MessageRole
    content: str
    tool_name: Optional[str] = Field(default=None, description="Set when role=tool.")
    tool_input: Optional[Dict[str, Any]] = None
    tool_result: Optional[str] = None


class Conversation(BaseModel):
    """
    Full conversation thread between an AI agent and a customer.

    Stored after each agent interaction so the owner dashboard can review
    what the AI said and the outcome achieved.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Ownership
    business_id: str
    customer_phone: str

    # Which specialist handled this conversation
    agent: str = Field(
        description="Agent name: orchestrator | lead_catcher | review_pilot | after_hours | booking_boss | campaign"
    )

    # Message thread
    messages: List[ConversationMessage] = Field(default_factory=list)

    # Metadata
    summary: Optional[str] = Field(default=None, description="One-line AI-generated summary.")
    last_message: str = Field(default="", description="Content of the last message for quick display.")
    outcome: Optional[str] = Field(
        default=None,
        description="Terminal outcome: appointment_booked | review_responded | lead_qualified | campaign_sent | faq_answered | no_show_recovered",
    )
    response_time_seconds: Optional[float] = None

    # Inbound event that triggered this conversation
    trigger_event_id: Optional[str] = None
