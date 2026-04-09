"""
API routes package.

Routers exported from here are registered in backend/server.py.
Each module owns a single FastAPI APIRouter.
"""

from backend.src.api.routes import (
    businesses,
    conversations,
    customers,
    dashboard,
    events,
)

__all__ = ["businesses", "conversations", "customers", "dashboard", "events"]
