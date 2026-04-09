"""
SQLAlchemy declarative base.

All ORM models inherit from `Base`. This module is imported by Alembic's
env.py so that autogenerate can detect table changes.

Note: For the hackathon demo we default to USE_IN_MEMORY_STORE=True so
this module is only active when running against a real Postgres instance.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase, MappedColumn, mapped_column
from sqlalchemy import String, DateTime, func
import uuid


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


# ---------------------------------------------------------------------------
# Mixin for common columns (id, created_at, updated_at)
# ---------------------------------------------------------------------------

class TimestampMixin:
    """Adds created_at and updated_at columns to any ORM model."""

    created_at: MappedColumn = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: MappedColumn = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UUIDPrimaryKeyMixin:
    """Adds a UUID primary key column."""

    id: MappedColumn = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
