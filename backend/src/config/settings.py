"""
Application settings — all configuration sourced from environment variables.

Uses Pydantic BaseSettings so values can be overridden via:
  1. Real environment variables
  2. A .env file in the project root
  3. Defaults defined here (safe for local/demo usage)

Usage:
    from backend.src.config import settings
    print(settings.ANTHROPIC_API_KEY)
"""

from __future__ import annotations

from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central settings object — one instance lives at backend.src.config.settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # Anthropic / AI
    # ------------------------------------------------------------------ #
    ANTHROPIC_API_KEY: str = Field(
        default="",
        description="Anthropic API key — required in production.",
    )
    OPENAI_API_KEY: str = Field(
        default="",
        description="OpenAI API key — used for Realtime voice sessions.",
    )
    LITELLM_API_KEY: str = Field(
        default="",
        description="LiteLLM proxy API key.",
    )
    LITELLM_BASE_URL: str = Field(
        default="https://llm.t-mobile.com",
        description="LiteLLM proxy base URL.",
    )
    LITELLM_MODEL: str = Field(
        default="gpt-4o-mini",
        description="Model to use via LiteLLM proxy.",
    )
    ANTHROPIC_MODEL: str = Field(
        default="claude-sonnet-4-6",
        description="Claude model to use for all agents.",
    )
    ANTHROPIC_MAX_TOKENS: int = Field(
        default=1024,
        description="Default max tokens for Claude responses.",
    )

    # ------------------------------------------------------------------ #
    # T-Mobile / SMS
    # ------------------------------------------------------------------ #
    TMOBILE_SMS_API_KEY: str = Field(default="", description="T-Mobile SMS gateway API key.")
    TMOBILE_SMS_API_URL: str = Field(
        default="https://api.t-mobile.com/sms/v1",
        description="T-Mobile SMS API base URL.",
    )

    # Twilio fallback
    TWILIO_ACCOUNT_SID: str = Field(default="", description="Twilio Account SID.")
    TWILIO_AUTH_TOKEN: str = Field(default="", description="Twilio Auth Token.")
    TWILIO_PHONE_NUMBER: str = Field(default="", description="Twilio outbound phone number.")

    # Voice
    VOICE_WEBHOOK_BASE_URL: str = Field(
        default="http://localhost:8100",
        description="Public base URL for Twilio voice webhooks (ngrok URL or deployed domain).",
    )
    BUSINESS_NAME: str = Field(
        default="Andy Plumbing",
        description="Business name spoken in voice greetings.",
    )

    # ------------------------------------------------------------------ #
    # Google APIs
    # ------------------------------------------------------------------ #
    GOOGLE_CALENDAR_API_KEY: str = Field(default="", description="Google Calendar API key.")
    GOOGLE_CALENDAR_ID: str = Field(default="primary", description="Google Calendar ID.")
    GOOGLE_REVIEWS_API_KEY: str = Field(default="", description="Google My Business / Reviews API key.")
    GOOGLE_PLACE_ID: str = Field(default="", description="Google Place ID for the demo business.")

    # ------------------------------------------------------------------ #
    # SendGrid / Email
    # ------------------------------------------------------------------ #
    SENDGRID_API_KEY: str = Field(default="", description="SendGrid API key.")
    SENDGRID_FROM_EMAIL: str = Field(default="noreply@smbinabox.com")
    SENDGRID_FROM_NAME: str = Field(default="SMB-in-a-Box")

    # ------------------------------------------------------------------ #
    # Database
    # ------------------------------------------------------------------ #
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://smb:smb@localhost:5432/smbinabox",
        description="Async SQLAlchemy database URL.",
    )

    # ------------------------------------------------------------------ #
    # Hackathon / Demo flags
    # ------------------------------------------------------------------ #
    USE_IN_MEMORY_STORE: bool = Field(
        default=True,
        description=(
            "When True, use the in-memory store instead of Postgres. "
            "Perfect for hackathon demos — no DB setup required."
        ),
    )
    DEMO_BUSINESS_ID: str = Field(
        default="demo-petes-plumbing",
        description="Business ID pre-loaded in the in-memory store.",
    )

    # ------------------------------------------------------------------ #
    # App / server
    # ------------------------------------------------------------------ #
    APP_ENV: str = Field(default="development", description="Application environment.")
    APP_HOST: str = Field(default="0.0.0.0")
    APP_PORT: int = Field(default=8000)
    LOG_LEVEL: str = Field(default="INFO")
    SECRET_KEY: str = Field(
        default="dev-secret-change-in-production",
        description="Secret key for JWT signing.",
    )

    # ------------------------------------------------------------------ #
    # CORS
    # ------------------------------------------------------------------ #
    ALLOWED_ORIGINS: str = Field(
        default="http://localhost:3000,http://localhost:5173",
        description="Comma-separated list of allowed CORS origins.",
    )

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_origins(cls, v: str) -> str:
        """Accept comma-separated or JSON-array string."""
        return v

    @property
    def origins_list(self) -> List[str]:
        """Return ALLOWED_ORIGINS as a Python list."""
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() == "production"

    @property
    def is_development(self) -> bool:
        return self.APP_ENV.lower() == "development"
