"""
Auth endpoint — login by business email.

For this POC there is no real password verification; any password is accepted.
The endpoint looks up the business whose `email` matches the submitted address
and returns the business ID so the frontend can load the correct dashboard.

POST /api/v1/auth/login
  Body:  { email: str, password: str }
  200:   { business_id, business_name, owner_name }
  401:   { detail: "No account found for that email." }
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

router = APIRouter()


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    business_id: str
    business_name: str
    owner_name: str | None = None


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, request: Request) -> LoginResponse:
    store = request.app.state.store
    businesses = await store.list_businesses()

    # Match on email (case-insensitive)
    match = next(
        (b for b in businesses if (b.email or "").lower() == payload.email.strip().lower()),
        None,
    )

    if not match:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No account found for that email address.",
        )

    return LoginResponse(
        business_id=match.id,
        business_name=match.name,
        owner_name=match.owner_name,
    )
