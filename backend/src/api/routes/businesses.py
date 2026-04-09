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
from typing import List

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from backend.src.models.business import Business, BusinessCreate, BusinessUpdate

logger = logging.getLogger(__name__)
router = APIRouter()


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
    business = Business(**payload.model_dump())
    await store.save_business(business)
    logger.info("Created business id=%s name=%s", business.id, business.name)
    return business


@router.put("/{business_id}", response_model=Business, summary="Update a business profile")
async def update_business(
    business_id: str, payload: BusinessUpdate, request: Request
) -> Business:
    store = request.app.state.store
    existing = await store.get_business(business_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Business '{business_id}' not found.")

    update_data = payload.model_dump(exclude_unset=True)
    updated = existing.model_copy(update=update_data)
    await store.save_business(updated)
    return updated


@router.delete("/{business_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None, summary="Delete a business")
async def delete_business(business_id: str, request: Request) -> None:
    store = request.app.state.store
    existing = await store.get_business(business_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Business '{business_id}' not found.")
    await store.delete_business(business_id)
    logger.info("Deleted business id=%s", business_id)
