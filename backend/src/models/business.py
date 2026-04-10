"""
Business profile model.

Represents a small/medium business using SMB-in-a-Box.
Contains all the context AI agents need to represent the business accurately:
  - Name, industry, location
  - Hours of operation
  - Services offered
  - FAQs (used by AfterHours agent)
  - Review response tone preferences
  - Booking settings
  - Campaign opt-in list
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class BusinessHours(BaseModel):
    """Operating hours for a single day."""
    open: str = Field(default="09:00", description="Opening time in HH:MM (local).")
    close: str = Field(default="17:00", description="Closing time in HH:MM (local).")
    closed: bool = Field(default=False, description="True if the business is closed this day.")


class FAQ(BaseModel):
    question: str
    answer: str


class ServiceItem(BaseModel):
    """A single service the business offers."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str = ""


class Business(BaseModel):
    """Full business profile — used as read model."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Identity
    name: str = Field(..., description="Business display name.")
    industry: str = Field(default="general", description="E.g. plumbing, salon, dental.")
    owner_name: str = Field(default="", description="Owner's first name for personal tone.")
    tagline: str = Field(default="", description="Short tagline shown in greetings.")
    description: str = Field(default="", description="Longer business description for agent context.")
    phone: str = Field(default="", description="Business phone in E.164.")
    email: Optional[str] = Field(default=None)
    website: Optional[str] = Field(default=None)
    address: Optional[str] = Field(default=None)
    timezone: str = Field(default="America/New_York")

    # T-Mobile / Twilio numbers assigned to this business
    sms_number: str = Field(default="", description="The SMS number customers text to reach this biz.")

    # Operating hours: keys are 'monday'..'sunday'
    hours: Dict[str, BusinessHours] = Field(default_factory=dict)

    # Services offered
    services: List[ServiceItem] = Field(default_factory=list)

    # AI personality
    brand_voice: str = Field(
        default="friendly and professional",
        description="Tone instructions for all AI responses.",
    )
    faqs: List[FAQ] = Field(default_factory=list)

    # Feature flags
    lead_capture_enabled: bool = True
    review_responses_enabled: bool = True
    after_hours_enabled: bool = True
    booking_enabled: bool = True
    campaigns_enabled: bool = True

    # Google integration
    google_place_id: Optional[str] = None

    # Booking settings
    appointment_duration_minutes: int = 60
    booking_buffer_minutes: int = 15

    # Campaign settings
    win_back_days: int = 90


class BusinessCreate(BaseModel):
    """
    Payload to create a new business.
    Accepts the full wizard payload including services, hours (wizard or standard
    format), tagline, description, and enabled_agents list.
    """
    id: Optional[str] = None
    name: str
    industry: str = "general"
    owner_name: str = ""
    tagline: str = ""
    description: str = ""
    phone: str = ""
    email: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None
    timezone: str = "America/New_York"
    sms_number: str = ""
    brand_voice: str = "friendly and professional"
    faqs: List[FAQ] = Field(default_factory=list)
    services: List[Any] = Field(default_factory=list)
    # hours accepts both wizard format {0: {openTime, closeTime, open}} and
    # standard format {monday: {open, close, closed}}
    hours: Optional[Any] = None
    # enabled_agents from wizard — mapped to feature flags
    enabled_agents: Optional[List[str]] = None


class BusinessUpdate(BaseModel):
    """Partial update payload — all fields optional."""
    name: Optional[str] = None
    industry: Optional[str] = None
    owner_name: Optional[str] = None
    tagline: Optional[str] = None
    description: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None
    timezone: Optional[str] = None
    brand_voice: Optional[str] = None
    faqs: Optional[List[FAQ]] = None
    services: Optional[List[Any]] = None
    hours: Optional[Any] = None
    lead_capture_enabled: Optional[bool] = None
    review_responses_enabled: Optional[bool] = None
    after_hours_enabled: Optional[bool] = None
    booking_enabled: Optional[bool] = None
    campaigns_enabled: Optional[bool] = None
    win_back_days: Optional[int] = None
    enabled_agents: Optional[List[str]] = None
