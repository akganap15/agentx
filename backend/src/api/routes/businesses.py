"""
Business profile CRUD endpoints.

Routes:
  GET    /api/v1/businesses/{business_id}   — fetch a business profile
  POST   /api/v1/businesses/                — create a new business profile
  PUT    /api/v1/businesses/{business_id}   — update a business profile
  DELETE /api/v1/businesses/{business_id}   — soft-delete a business profile
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, status

from backend.src.models.business import (
    Business, BusinessCreate, BusinessHours, BusinessUpdate, ServiceItem,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Day index → name mapping used by the setup wizard
_DAY_NAMES = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday']

# Agent key → feature flag mapping
_AGENT_FLAGS = {
    'lead_catcher':  'lead_capture_enabled',
    'review_pilot':  'review_responses_enabled',
    'after_hours':   'after_hours_enabled',
    'booking_boss':  'booking_enabled',
    'campaign':      'campaigns_enabled',
}


def _normalize_hours(raw: Any) -> Dict[str, BusinessHours]:
    """
    Accept hours in two formats:
      Wizard format  — {0: {open: true, openTime: "08:00", closeTime: "18:00"}, ...}
      Standard format — {monday: {open: "08:00", close: "17:00", closed: false}, ...}
    Always returns {monday: BusinessHours, ...}.
    """
    if not raw:
        return {}
    out: Dict[str, BusinessHours] = {}
    first_key = next(iter(raw), None)
    wizard_format = first_key is not None and str(first_key).isdigit()

    for key, h in raw.items():
        if not isinstance(h, dict):
            continue
        day_name = _DAY_NAMES[int(key)] if wizard_format else str(key).lower()
        if wizard_format:
            out[day_name] = BusinessHours(
                open=h.get("openTime", "09:00"),
                close=h.get("closeTime", "17:00"),
                closed=not h.get("open", True),
            )
        else:
            out[day_name] = BusinessHours(
                open=h.get("open", h.get("openTime", "09:00")),
                close=h.get("close", h.get("closeTime", "17:00")),
                closed=h.get("closed", False),
            )
    return out


def _normalize_services(raw: List[Any]) -> List[ServiceItem]:
    """Coerce list of dicts (from wizard) into ServiceItem objects."""
    out = []
    for s in raw or []:
        if isinstance(s, dict) and s.get("name", "").strip():
            out.append(ServiceItem(
                id=s.get("id", ""),
                name=s["name"].strip(),
                description=s.get("description", ""),
            ))
        elif isinstance(s, ServiceItem):
            out.append(s)
    return out


def _apply_enabled_agents(fields: dict, enabled_agents: Optional[List[str]]) -> None:
    """Map enabled_agents list to individual feature flag fields."""
    if enabled_agents is None:
        return
    for agent_key, flag in _AGENT_FLAGS.items():
        fields[flag] = agent_key in enabled_agents


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/{business_id}", response_model=Business, summary="Get a business profile")
async def get_business(business_id: str, request: Request) -> Business:
    store = request.app.state.store
    business = await store.get_business(business_id)
    if not business:
        raise HTTPException(status_code=404, detail=f"Business '{business_id}' not found.")
    return business


@router.get("/", response_model=List[Business], summary="List all businesses (demo)")
async def list_businesses(request: Request) -> List[Business]:
    store = request.app.state.store
    return await store.list_businesses()


@router.post(
    "/",
    response_model=Business,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new business profile",
)
async def create_business(payload: BusinessCreate, request: Request) -> Business:
    store = request.app.state.store

    fields = payload.model_dump(exclude={"id", "hours", "services", "enabled_agents"})

    # Use custom ID from wizard if provided
    biz_id = payload.id or None

    # Normalize hours and services from wizard format
    fields["hours"] = _normalize_hours(payload.hours)
    fields["services"] = _normalize_services(payload.services)

    # Map enabled_agents → feature flags
    _apply_enabled_agents(fields, payload.enabled_agents)

    business = Business(id=biz_id, **fields) if biz_id else Business(**fields)
    await store.save_business(business)
    logger.info("Created business id=%s name=%s services=%d",
                business.id, business.name, len(business.services))
    return business


@router.put("/{business_id}", response_model=Business, summary="Update a business profile")
async def update_business(
    business_id: str, payload: BusinessUpdate, request: Request
) -> Business:
    store = request.app.state.store
    existing = await store.get_business(business_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Business '{business_id}' not found.")

    update_data = payload.model_dump(exclude_unset=True, exclude={"hours", "services", "enabled_agents"})

    if payload.hours is not None:
        update_data["hours"] = _normalize_hours(payload.hours)
    if payload.services is not None:
        update_data["services"] = _normalize_services(payload.services)
    if payload.enabled_agents is not None:
        _apply_enabled_agents(update_data, payload.enabled_agents)

    updated = existing.model_copy(update=update_data)
    await store.save_business(updated)
    logger.info("Updated business id=%s", business_id)
    return updated


@router.delete("/{business_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None, summary="Delete a business")
async def delete_business(business_id: str, request: Request) -> None:
    store = request.app.state.store
    existing = await store.get_business(business_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Business '{business_id}' not found.")
    await store.delete_business(business_id)
    logger.info("Deleted business id=%s", business_id)
