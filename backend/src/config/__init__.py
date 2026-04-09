"""
Configuration package — exposes the singleton Settings instance.

Usage:
    from backend.src.config import settings

    print(settings.ANTHROPIC_MODEL)
"""

from backend.src.config.settings import Settings

# Module-level singleton — imported everywhere
settings = Settings()

__all__ = ["settings", "Settings"]
