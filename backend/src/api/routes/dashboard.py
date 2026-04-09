"""
Owner dashboard data endpoints.

These endpoints power the tablet/mobile owner dashboard, providing
aggregated metrics and recent activity for the business owner.

Routes:
  GET /api/v1/dashboard/{business_id}/summary   — KPI summary card
  GET /api/v1/dashboard/{business_id}/activity  — recent conversation feed
  GET /api/v1/dashboard/{business_id}/leads     — lead pipeline
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class KPISummary(BaseModel):
    business_id: str
    period_start: datetime
    period_end: datetime
    total_conversations: int = 0
    leads_captured: int = 0
    appointments_booked: int = 0
    reviews_responded: int = 0
    no_shows_recovered: int = 0
    campaigns_sent: int = 0
    after_hours_handled: int = 0
    avg_response_time_seconds: Optional[float] = None


class ActivityItem(BaseModel):
    timestamp: datetime
    agent: str
    customer_phone: str
    summary: str
    outcome: Optional[str] = None


class LeadItem(BaseModel):
    customer_phone: str
    customer_name: Optional[str] = None
    stage: str  # new | qualified | appointment_booked | closed
    created_at: datetime
    last_contact: Optional[datetime] = None
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/{business_id}/summary", response_model=KPISummary, summary="KPI summary for owner dashboard")
async def get_summary(
    business_id: str,
    request: Request,
    days: int = Query(default=7, ge=1, le=90, description="Look-back window in days"),
) -> KPISummary:
    """
    Returns key performance indicators aggregated over the requested window.
    In the hackathon demo this is derived from the in-memory conversation store.
    """
    store = request.app.state.store
    business = await store.get_business(business_id)
    if not business:
        raise HTTPException(status_code=404, detail=f"Business '{business_id}' not found.")

    now = datetime.utcnow()
    period_start = now - timedelta(days=days)

    conversations = await store.get_conversations(business_id)
    recent = [c for c in conversations if c.created_at >= period_start]

    # Tally by agent type
    summary = KPISummary(
        business_id=business_id,
        period_start=period_start,
        period_end=now,
        total_conversations=len(recent),
        leads_captured=sum(1 for c in recent if c.agent == "lead_catcher"),
        appointments_booked=sum(1 for c in recent if c.outcome == "appointment_booked"),
        reviews_responded=sum(1 for c in recent if c.agent == "review_pilot"),
        no_shows_recovered=sum(1 for c in recent if c.agent == "booking_boss"),
        campaigns_sent=sum(1 for c in recent if c.agent == "campaign"),
        after_hours_handled=sum(1 for c in recent if c.agent == "after_hours"),
    )

    if recent:
        response_times = [c.response_time_seconds for c in recent if c.response_time_seconds]
        if response_times:
            summary.avg_response_time_seconds = sum(response_times) / len(response_times)

    return summary


@router.get("/{business_id}/activity", response_model=List[ActivityItem], summary="Recent activity feed")
async def get_activity(
    business_id: str,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> List[ActivityItem]:
    """Returns the N most recent conversations for the activity feed."""
    store = request.app.state.store
    conversations = await store.get_conversations(business_id)
    recent = sorted(conversations, key=lambda c: c.created_at, reverse=True)[:limit]

    return [
        ActivityItem(
            timestamp=c.created_at,
            agent=c.agent,
            customer_phone=c.customer_phone,
            summary=c.summary or c.last_message[:80],
            outcome=c.outcome,
        )
        for c in recent
    ]


@router.get("/{business_id}/leads", response_model=List[LeadItem], summary="Lead pipeline")
async def get_leads(
    business_id: str,
    request: Request,
    stage: Optional[str] = Query(default=None, description="Filter by stage"),
) -> List[LeadItem]:
    """Returns the lead pipeline for the business."""
    store = request.app.state.store
    customers = await store.list_customers(business_id=business_id)

    leads = [c for c in customers if c.is_lead]
    if stage:
        leads = [c for c in leads if c.lead_stage == stage]

    return [
        LeadItem(
            customer_phone=c.phone,
            customer_name=c.name,
            stage=c.lead_stage or "new",
            created_at=c.created_at,
            last_contact=c.last_contact_at,
            notes=c.notes,
        )
        for c in leads
    ]
