"""
SQLAlchemy ORM models for SQLite persistence.

Maps the three core Pydantic models (Business, Customer, Conversation) to
SQL tables. Complex nested fields (hours, faqs, messages) are stored as JSON
columns — simple, queryable, no extra tables needed for a POC.

Tables created automatically on startup via Base.metadata.create_all().
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.db.base import Base

# Inline timestamps — avoids SQLAlchemy 2.0 Mapped[] annotation conflict with TimestampMixin
_TS = {"server_default": func.now(), "nullable": False}
_TS_UPDATE = {**_TS}  # updated_at set explicitly in Python before each save


class BusinessORM(Base):
    __tablename__ = "businesses"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **_TS)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **_TS_UPDATE)
    name: Mapped[str] = mapped_column(String(255))
    industry: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    owner_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    website: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    timezone: Mapped[str] = mapped_column(String(50), default="UTC")
    sms_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    tagline: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    brand_voice: Mapped[str] = mapped_column(Text, default="")
    google_place_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    appointment_duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    booking_buffer_minutes: Mapped[int] = mapped_column(Integer, default=15)
    win_back_days: Mapped[int] = mapped_column(Integer, default=90)
    lead_capture_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    review_responses_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    after_hours_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    booking_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    campaigns_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # JSON blobs for nested structures
    hours: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    faqs: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    services: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)


class CustomerORM(Base):
    __tablename__ = "customers"

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **_TS)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **_TS_UPDATE)

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    phone: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    business_id: Mapped[str] = mapped_column(String(100), index=True)
    is_lead: Mapped[bool] = mapped_column(Boolean, default=True)
    lead_stage: Mapped[str] = mapped_column(String(50), default="new")
    first_contact_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_contact_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    total_visits: Mapped[int] = mapped_column(Integer, default=0)
    last_visit_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    opted_in_sms: Mapped[bool] = mapped_column(Boolean, default=True)
    opted_in_email: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    upcoming_appointment: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    no_show_count: Mapped[int] = mapped_column(Integer, default=0)


class ConversationORM(Base):
    __tablename__ = "conversations"

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **_TS)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **_TS_UPDATE)

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    business_id: Mapped[str] = mapped_column(String(100), index=True)
    customer_phone: Mapped[str] = mapped_column(String(50), index=True)
    agent: Mapped[str] = mapped_column(String(50))
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_message: Mapped[str] = mapped_column(Text, default="")
    outcome: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    response_time_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    trigger_event_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Full message thread stored as JSON list
    messages: Mapped[list] = mapped_column(JSON, default=list)
