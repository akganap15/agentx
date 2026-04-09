"""
Customer model.

Represents a contact in a business's customer base.
Tracks lead stage, visit history, and opt-in status for campaigns.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class Customer(BaseModel):
    """Full customer record."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Identity
    phone: str = Field(..., description="Primary identifier — E.164 phone number.")
    name: Optional[str] = Field(default=None)
    email: Optional[str] = Field(default=None)

    # Business association
    business_id: str = Field(..., description="Which business this customer belongs to.")

    # Lead tracking
    is_lead: bool = Field(default=True, description="True until the customer converts.")
    lead_stage: str = Field(
        default="new",
        description="Pipeline stage: new | contacted | qualified | appointment_booked | closed",
    )

    # Engagement history
    first_contact_at: Optional[datetime] = None
    last_contact_at: Optional[datetime] = None
    total_visits: int = 0
    last_visit_at: Optional[datetime] = None

    # Preferences
    opted_in_sms: bool = True
    opted_in_email: bool = False

    # Freeform notes (from agent summaries)
    notes: Optional[str] = None

    # Appointment tracking
    upcoming_appointment: Optional[datetime] = None
    no_show_count: int = 0


class CustomerCreate(BaseModel):
    phone: str
    name: Optional[str] = None
    email: Optional[str] = None
    business_id: str
    is_lead: bool = True
    lead_stage: str = "new"
    opted_in_sms: bool = True
    opted_in_email: bool = False
    notes: Optional[str] = None


class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    is_lead: Optional[bool] = None
    lead_stage: Optional[str] = None
    opted_in_sms: Optional[bool] = None
    opted_in_email: Optional[bool] = None
    notes: Optional[str] = None
    last_contact_at: Optional[datetime] = None
    last_visit_at: Optional[datetime] = None
    total_visits: Optional[int] = None
    no_show_count: Optional[int] = None
    upcoming_appointment: Optional[datetime] = None
