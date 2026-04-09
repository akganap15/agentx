"""
Customer management endpoints.

Routes:
  GET    /api/v1/customers/{phone}                   — look up a customer by phone
  GET    /api/v1/customers/?business_id=xxx          — list customers for a business
  POST   /api/v1/customers/                          — create/upsert a customer
  PUT    /api/v1/customers/{phone}                   — update a customer record
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status

from backend.src.models.customer import Customer, CustomerCreate, CustomerUpdate

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/{phone}", response_model=Customer, summary="Get customer by phone number")
async def get_customer(phone: str, request: Request) -> Customer:
    """Look up a customer record by E.164 phone number."""
    store = request.app.state.store
    # Normalise: strip spaces, ensure +
    normalized = phone.strip().replace(" ", "")
    customer = await store.get_customer(normalized)
    if not customer:
        raise HTTPException(status_code=404, detail=f"Customer with phone '{phone}' not found.")
    return customer


@router.get("/", response_model=List[Customer], summary="List customers for a business")
async def list_customers(
    request: Request,
    business_id: Optional[str] = Query(default=None, description="Filter by business ID"),
) -> List[Customer]:
    store = request.app.state.store
    return await store.list_customers(business_id=business_id)


@router.post(
    "/",
    response_model=Customer,
    status_code=status.HTTP_201_CREATED,
    summary="Create or update a customer",
)
async def create_customer(payload: CustomerCreate, request: Request) -> Customer:
    store = request.app.state.store

    # Upsert: if a customer with this phone already exists, update it
    existing = await store.get_customer(payload.phone)
    if existing:
        update_data = payload.model_dump(exclude_unset=True)
        customer = existing.model_copy(update=update_data)
    else:
        customer = Customer(**payload.model_dump())

    await store.save_customer(customer)
    logger.info("Upserted customer phone=%s business=%s", customer.phone, customer.business_id)
    return customer


@router.put("/{phone}", response_model=Customer, summary="Update a customer record")
async def update_customer(phone: str, payload: CustomerUpdate, request: Request) -> Customer:
    store = request.app.state.store
    existing = await store.get_customer(phone)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Customer '{phone}' not found.")

    update_data = payload.model_dump(exclude_unset=True)
    updated = existing.model_copy(update=update_data)
    await store.save_customer(updated)
    return updated
