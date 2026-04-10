"""
SQLite-backed persistent store.

Drop-in replacement for InMemoryStore — exposes exactly the same async
interface so all routes and agents work without any changes.

Data is persisted to `tchai.db` (SQLite file) via async SQLAlchemy.
Complex nested fields (hours, faqs, messages) are serialized as JSON columns.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.src.models.business import Business, BusinessHours, FAQ, ServiceItem
from backend.src.models.conversation import Conversation, ConversationMessage, MessageRole
from backend.src.models.customer import Customer
from backend.src.db.models import BusinessORM, CustomerORM, ConversationORM

logger = logging.getLogger(__name__)


class SQLiteStore:
    """
    Async SQLite store backed by SQLAlchemy.

    Each method opens a short-lived session, commits, and closes — keeping
    connection usage minimal for a single-process server.
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._factory = session_factory

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _session(self) -> AsyncSession:
        return self._factory()

    # ---- Business converters ------------------------------------------ #

    def _biz_to_orm(self, b: Business) -> BusinessORM:
        return BusinessORM(
            id=b.id,
            name=b.name,
            industry=b.industry,
            owner_name=b.owner_name,
            tagline=b.tagline,
            description=b.description,
            phone=b.phone,
            email=b.email,
            website=b.website,
            address=b.address,
            timezone=b.timezone,
            sms_number=b.sms_number,
            brand_voice=b.brand_voice,
            google_place_id=b.google_place_id,
            appointment_duration_minutes=b.appointment_duration_minutes,
            booking_buffer_minutes=b.booking_buffer_minutes,
            win_back_days=b.win_back_days,
            lead_capture_enabled=b.lead_capture_enabled,
            review_responses_enabled=b.review_responses_enabled,
            after_hours_enabled=b.after_hours_enabled,
            booking_enabled=b.booking_enabled,
            campaigns_enabled=b.campaigns_enabled,
            hours={k: v.model_dump() for k, v in b.hours.items()} if b.hours else None,
            faqs=[f.model_dump() for f in b.faqs] if b.faqs else [],
            services=[s.model_dump() for s in b.services] if b.services else [],
        )

    def _orm_to_biz(self, row: BusinessORM) -> Business:
        hours: Dict[str, BusinessHours] = {}
        if row.hours:
            for day, h in row.hours.items():
                hours[day] = BusinessHours(**h)
        faqs: List[FAQ] = [FAQ(**f) for f in (row.faqs or [])]
        services: List[ServiceItem] = [ServiceItem(**s) for s in (row.services or [])]
        return Business(
            id=row.id,
            created_at=row.created_at or datetime.utcnow(),
            updated_at=row.updated_at or datetime.utcnow(),
            name=row.name,
            industry=row.industry,
            owner_name=row.owner_name,
            tagline=getattr(row, "tagline", "") or "",
            description=getattr(row, "description", "") or "",
            phone=row.phone,
            email=row.email,
            website=row.website,
            address=row.address,
            timezone=row.timezone or "UTC",
            sms_number=row.sms_number,
            brand_voice=row.brand_voice or "",
            google_place_id=row.google_place_id,
            appointment_duration_minutes=row.appointment_duration_minutes or 60,
            booking_buffer_minutes=row.booking_buffer_minutes or 15,
            win_back_days=row.win_back_days or 90,
            lead_capture_enabled=row.lead_capture_enabled,
            review_responses_enabled=row.review_responses_enabled,
            after_hours_enabled=row.after_hours_enabled,
            booking_enabled=row.booking_enabled,
            campaigns_enabled=row.campaigns_enabled,
            hours=hours,
            faqs=faqs,
            services=services,
        )

    # ---- Customer converters ------------------------------------------ #

    def _cust_to_orm(self, c: Customer) -> CustomerORM:
        return CustomerORM(
            id=c.id,
            phone=c.phone,
            name=c.name,
            email=c.email,
            business_id=c.business_id,
            is_lead=c.is_lead,
            lead_stage=c.lead_stage,
            first_contact_at=c.first_contact_at,
            last_contact_at=c.last_contact_at,
            total_visits=c.total_visits,
            last_visit_at=c.last_visit_at,
            opted_in_sms=c.opted_in_sms,
            opted_in_email=c.opted_in_email,
            notes=c.notes,
            upcoming_appointment=c.upcoming_appointment,
            no_show_count=c.no_show_count,
        )

    def _orm_to_cust(self, row: CustomerORM) -> Customer:
        return Customer(
            id=row.id,
            created_at=row.created_at or datetime.utcnow(),
            updated_at=row.updated_at or datetime.utcnow(),
            phone=row.phone,
            name=row.name,
            email=row.email,
            business_id=row.business_id,
            is_lead=row.is_lead,
            lead_stage=row.lead_stage or "new",
            first_contact_at=row.first_contact_at,
            last_contact_at=row.last_contact_at,
            total_visits=row.total_visits or 0,
            last_visit_at=row.last_visit_at,
            opted_in_sms=row.opted_in_sms,
            opted_in_email=row.opted_in_email,
            notes=row.notes,
            upcoming_appointment=row.upcoming_appointment,
            no_show_count=row.no_show_count or 0,
        )

    # ---- Conversation converters -------------------------------------- #

    def _conv_to_orm(self, c: Conversation) -> ConversationORM:
        return ConversationORM(
            id=c.id,
            business_id=c.business_id,
            customer_phone=c.customer_phone,
            agent=c.agent,
            summary=c.summary,
            last_message=c.last_message,
            outcome=c.outcome,
            response_time_seconds=c.response_time_seconds,
            trigger_event_id=c.trigger_event_id,
            messages=[self._msg_to_dict(m) for m in c.messages],
        )

    def _orm_to_conv(self, row: ConversationORM) -> Conversation:
        return Conversation(
            id=row.id,
            created_at=row.created_at or datetime.utcnow(),
            updated_at=row.updated_at or datetime.utcnow(),
            business_id=row.business_id,
            customer_phone=row.customer_phone,
            agent=row.agent,
            summary=row.summary,
            last_message=row.last_message or "",
            outcome=row.outcome,
            response_time_seconds=row.response_time_seconds,
            trigger_event_id=row.trigger_event_id,
            messages=[self._dict_to_msg(m) for m in (row.messages or [])],
        )

    @staticmethod
    def _msg_to_dict(m: ConversationMessage) -> Dict[str, Any]:
        d = m.model_dump()
        d["timestamp"] = d["timestamp"].isoformat() if d.get("timestamp") else None
        return d

    @staticmethod
    def _dict_to_msg(d: Dict[str, Any]) -> ConversationMessage:
        if isinstance(d.get("timestamp"), str):
            d = {**d, "timestamp": datetime.fromisoformat(d["timestamp"])}
        return ConversationMessage(**d)

    # ------------------------------------------------------------------ #
    # Business operations
    # ------------------------------------------------------------------ #

    async def get_business(self, business_id: str) -> Optional[Business]:
        async with self._session() as s:
            row = await s.get(BusinessORM, business_id)
            return self._orm_to_biz(row) if row else None

    async def save_business(self, business: Business) -> Business:
        now = datetime.utcnow()
        business.updated_at = now
        async with self._session() as s:
            existing = await s.get(BusinessORM, business.id)
            if existing:
                orm = self._biz_to_orm(business)
                for col in BusinessORM.__table__.columns:
                    if col.name not in ("id", "created_at", "updated_at"):
                        setattr(existing, col.name, getattr(orm, col.name))
                existing.updated_at = now
            else:
                s.add(self._biz_to_orm(business))
            await s.commit()
        return business

    async def list_businesses(self) -> List[Business]:
        async with self._session() as s:
            result = await s.execute(select(BusinessORM))
            return [self._orm_to_biz(r) for r in result.scalars().all()]

    async def delete_business(self, business_id: str) -> None:
        async with self._session() as s:
            row = await s.get(BusinessORM, business_id)
            if row:
                await s.delete(row)
                await s.commit()

    # ------------------------------------------------------------------ #
    # Customer operations
    # ------------------------------------------------------------------ #

    async def get_customer(self, phone: str) -> Optional[Customer]:
        async with self._session() as s:
            result = await s.execute(
                select(CustomerORM).where(CustomerORM.phone == phone)
            )
            row = result.scalar_one_or_none()
            return self._orm_to_cust(row) if row else None

    async def save_customer(self, customer: Customer) -> Customer:
        now = datetime.utcnow()
        customer.updated_at = now
        async with self._session() as s:
            result = await s.execute(
                select(CustomerORM).where(CustomerORM.phone == customer.phone)
            )
            existing = result.scalar_one_or_none()
            if existing:
                orm = self._cust_to_orm(customer)
                for col in CustomerORM.__table__.columns:
                    if col.name not in ("id", "created_at", "updated_at"):
                        setattr(existing, col.name, getattr(orm, col.name))
                existing.updated_at = now
            else:
                s.add(self._cust_to_orm(customer))
            await s.commit()
        return customer

    async def list_customers(self, business_id: Optional[str] = None) -> List[Customer]:
        async with self._session() as s:
            q = select(CustomerORM)
            if business_id:
                q = q.where(CustomerORM.business_id == business_id)
            result = await s.execute(q)
            return [self._orm_to_cust(r) for r in result.scalars().all()]

    # ------------------------------------------------------------------ #
    # Conversation operations
    # ------------------------------------------------------------------ #

    async def get_conversations(self, business_id: str) -> List[Conversation]:
        async with self._session() as s:
            result = await s.execute(
                select(ConversationORM).where(ConversationORM.business_id == business_id)
            )
            return [self._orm_to_conv(r) for r in result.scalars().all()]

    async def save_conversation(self, conv: Conversation) -> Conversation:
        now = datetime.utcnow()
        conv.updated_at = now
        async with self._session() as s:
            existing = await s.get(ConversationORM, conv.id)
            if existing:
                orm = self._conv_to_orm(conv)
                for col in ConversationORM.__table__.columns:
                    if col.name not in ("id", "created_at", "updated_at"):
                        setattr(existing, col.name, getattr(orm, col.name))
                existing.updated_at = now
            else:
                s.add(self._conv_to_orm(conv))
            await s.commit()
        return conv

    async def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        async with self._session() as s:
            row = await s.get(ConversationORM, conversation_id)
            return self._orm_to_conv(row) if row else None
