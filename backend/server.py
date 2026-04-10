"""
SMB-in-a-Box / T-CHai — FastAPI application entry point.

Startup sequence:
  1. Load settings from environment / .env
  2. Initialize database (or in-memory store)
  3. Register all API routers
  4. Configure CORS and middleware
  5. Expose health-check endpoints

Run locally:
    uvicorn backend.server:app --reload --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.src.config import settings
from backend.src.api.routes import (
    auth,
    businesses,
    conversations,
    customers,
    dashboard,
    events,
    retell_voice,
    voice,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# Quiet noisy third-party loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lifespan — runs at startup and shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialize resources on startup; clean up on shutdown."""
    logger.info("=== T-CHai SMB-in-a-Box starting up ===")
    logger.info("Model: %s", settings.ANTHROPIC_MODEL)
    logger.info("Store: %s", "in-memory" if settings.USE_IN_MEMORY_STORE else "sqlite")

    if settings.USE_IN_MEMORY_STORE:
        # Pre-populate the demo store so the hackathon demo works out of the box
        from backend.src.db.store import demo_store
        logger.info("Demo business loaded: %s", settings.DEMO_BUSINESS_ID)
        app.state.store = demo_store

        # Hard-coded demo appointment so the dashboard KPI is non-zero on a fresh start
        from backend.src.db.seed_appointment import seed_demo_appointment
        await seed_demo_appointment(demo_store)
    else:
        from backend.src.db.session import engine, async_session_factory
        from backend.src.db.base import Base
        # Import ORM models so metadata is populated before create_all
        import backend.src.db.models  # noqa: F401
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables ensured.")

        from backend.src.db.sqlite_store import SQLiteStore
        store = SQLiteStore(async_session_factory)
        app.state.store = store

        # Seed demo data on first run (only if no businesses exist yet)
        businesses = await store.list_businesses()
        if not businesses:
            logger.info("First run — seeding demo data into SQLite.")
            from backend.src.db.store import _build_demo_store
            demo = _build_demo_store()
            for biz in demo._businesses.values():
                await store.save_business(biz)
            for cust in demo._customers.values():
                await store.save_customer(cust)
            for conv in demo._conversations.values():
                await store.save_conversation(conv)
            logger.info("Demo seed complete.")

        # Hard-coded demo appointment so the dashboard KPI is non-zero
        from backend.src.db.seed_appointment import seed_demo_appointment
        await seed_demo_appointment(store)

    logger.info("=== T-CHai ready — listening on %s:%s ===", settings.APP_HOST, settings.APP_PORT)
    yield

    logger.info("=== T-CHai shutting down ===")
    if not settings.USE_IN_MEMORY_STORE:
        from backend.src.db.session import engine
        await engine.dispose()

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="T-CHai: SMB-in-a-Box",
    description=(
        "AI-powered business assistant for small/medium businesses. "
        "Handles leads, reviews, bookings, after-hours, and win-back campaigns "
        "via T-Mobile SMS — powered by Claude."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

API_PREFIX = "/api/v1"

app.include_router(auth.router,          prefix=f"{API_PREFIX}/auth",          tags=["Auth"])
app.include_router(events.router,        prefix=f"{API_PREFIX}/events",        tags=["Events"])
app.include_router(businesses.router,    prefix=f"{API_PREFIX}/businesses",    tags=["Businesses"])
app.include_router(customers.router,     prefix=f"{API_PREFIX}/customers",     tags=["Customers"])
app.include_router(dashboard.router,     prefix=f"{API_PREFIX}/dashboard",     tags=["Dashboard"])
app.include_router(conversations.router, prefix=f"{API_PREFIX}/conversations", tags=["Conversations"])
app.include_router(voice.router,         prefix=f"{API_PREFIX}/voice",         tags=["Voice"])
app.include_router(retell_voice.router,  prefix=f"{API_PREFIX}/voice/retell",  tags=["Retell Voice"])

# ---------------------------------------------------------------------------
# Health / meta endpoints
# ---------------------------------------------------------------------------

@app.get("/healthz", tags=["Health"])
async def health_check() -> JSONResponse:
    """Kubernetes liveness probe."""
    return JSONResponse({"status": "ok", "service": "tchai-api"})


@app.get("/readyz", tags=["Health"])
async def readiness_check() -> JSONResponse:
    """Kubernetes readiness probe."""
    return JSONResponse(
        {
            "status": "ready",
            "model": settings.ANTHROPIC_MODEL,
            "store": "memory" if settings.USE_IN_MEMORY_STORE else "postgres",
        }
    )


@app.get("/", tags=["Meta"])
async def root() -> JSONResponse:
    return JSONResponse(
        {
            "project": "SMB-in-a-Box",
            "tagline": "Your AI business assistant — powered by T-Mobile & Claude",
            "docs": "/docs",
        }
    )
