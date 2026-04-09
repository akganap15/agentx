"""
Database package.

Exports the appropriate store depending on settings.USE_IN_MEMORY_STORE:
  - True  → InMemoryStore  (no external dependencies, instant demo startup)
  - False → async SQLAlchemy session (requires Postgres)

Usage:
    from backend.src.db import get_store
"""

from backend.src.db.store import InMemoryStore, demo_store

__all__ = ["InMemoryStore", "demo_store"]
