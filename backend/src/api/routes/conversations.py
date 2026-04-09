"""
Conversation history endpoints.

Routes:
  GET /api/v1/conversations/{business_id}            — list conversations for a business
  GET /api/v1/conversations/{business_id}/{conv_id}  — full conversation thread
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from backend.src.models.conversation import Conversation

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/{business_id}", response_model=List[Conversation], summary="List conversations for a business")
async def list_conversations(
    business_id: str,
    request: Request,
    customer_phone: Optional[str] = Query(default=None, description="Filter by customer phone"),
    agent: Optional[str] = Query(default=None, description="Filter by agent type"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> List[Conversation]:
    """
    Returns paginated conversation records for the business.
    Optionally filter by customer phone number or agent type.
    """
    store = request.app.state.store
    business = await store.get_business(business_id)
    if not business:
        raise HTTPException(status_code=404, detail=f"Business '{business_id}' not found.")

    conversations = await store.get_conversations(business_id)

    if customer_phone:
        conversations = [c for c in conversations if c.customer_phone == customer_phone]
    if agent:
        conversations = [c for c in conversations if c.agent == agent]

    # Sort newest-first, then paginate
    conversations = sorted(conversations, key=lambda c: c.created_at, reverse=True)
    return conversations[offset : offset + limit]


@router.get(
    "/{business_id}/{conversation_id}",
    response_model=Conversation,
    summary="Get a single conversation thread",
)
async def get_conversation(business_id: str, conversation_id: str, request: Request) -> Conversation:
    """Returns a single conversation with its full message history."""
    store = request.app.state.store
    conversations = await store.get_conversations(business_id)
    conv = next((c for c in conversations if c.id == conversation_id), None)
    if not conv:
        raise HTTPException(
            status_code=404,
            detail=f"Conversation '{conversation_id}' not found for business '{business_id}'.",
        )
    return conv
