"""
Hard-coded demo appointment seed.

Adds a fake "Kelly Ann Smith — Water Heater Install & Inspection"
appointment for 10 AM tomorrow into:

  1. The conversation store, so the owner dashboard's
     "Appointments Booked" KPI shows >= 1 on a fresh start.
  2. Google Calendar (when GOOGLE_SERVICE_ACCOUNT_JSON is configured),
     so the appointment also appears in the business's actual calendar.

Idempotency:
  - In-memory store is reset on every restart, so the conversation seed is
    always fresh; no idempotency needed.
  - The Google Calendar event uses a deterministic per-day event ID so a
    second startup on the same day will just hit a 409 and skip creation.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from backend.src.config import settings
from backend.src.models.conversation import (
    Conversation,
    ConversationMessage,
    MessageRole,
)

logger = logging.getLogger(__name__)

DEMO_CUSTOMER_NAME = "Kelly Ann Smith"
DEMO_CUSTOMER_PHONE = "+15555550123"
DEMO_SERVICE = "Water Heater Install & Inspection"
DEMO_DURATION_MIN = 180  # 3 hours for install + inspection
TIMEZONE = "America/Los_Angeles"


async def seed_demo_appointment(store) -> None:
    """Seed both the conversation store and Google Calendar."""
    tz = ZoneInfo(TIMEZONE)
    tomorrow = datetime.now(tz) + timedelta(days=1)
    appt_start = tomorrow.replace(hour=10, minute=0, second=0, microsecond=0)
    appt_end = appt_start + timedelta(minutes=DEMO_DURATION_MIN)

    # 1) Conversation that drives the "Appointments Booked" KPI
    pretty_when = appt_start.strftime("%A %b %-d at %-I:%M %p")
    conv = Conversation(
        business_id=settings.DEMO_BUSINESS_ID,
        customer_phone=DEMO_CUSTOMER_PHONE,
        agent="booking_boss",
        summary=f"Booked: {DEMO_SERVICE} for {DEMO_CUSTOMER_NAME}",
        last_message=f"You're booked for {DEMO_SERVICE} on {pretty_when}.",
        outcome="appointment_booked",
        messages=[
            ConversationMessage(
                role=MessageRole.USER,
                content="Hi, I'd like to book a water heater install and inspection.",
            ),
            ConversationMessage(
                role=MessageRole.ASSISTANT,
                content="Great! I have 10 AM tomorrow available — shall I book that for you?",
            ),
            ConversationMessage(role=MessageRole.USER, content="Yes please."),
            ConversationMessage(
                role=MessageRole.ASSISTANT,
                content=f"All set, {DEMO_CUSTOMER_NAME}! See you tomorrow at 10 AM.",
            ),
        ],
    )
    await store.save_conversation(conv)
    logger.info("Seeded demo appointment conversation: %s", conv.id)

    # 2) Real Google Calendar event (best-effort — never crash startup)
    if settings.GOOGLE_SERVICE_ACCOUNT_JSON:
        try:
            _seed_calendar_event(appt_start, appt_end)
        except Exception as exc:
            logger.warning("Calendar seed failed (continuing without it): %s", exc)
    else:
        logger.info("GOOGLE_SERVICE_ACCOUNT_JSON not set — skipping calendar event seed.")


def _seed_calendar_event(start_dt: datetime, end_dt: datetime) -> None:
    """Create the Google Calendar event with a deterministic per-day ID."""
    from google.oauth2 import service_account  # type: ignore
    from google.auth.transport.requests import AuthorizedSession  # type: ignore

    scopes = ["https://www.googleapis.com/auth/calendar"]
    credentials = service_account.Credentials.from_service_account_file(
        settings.GOOGLE_SERVICE_ACCOUNT_JSON,
        scopes=scopes,
    )
    session = AuthorizedSession(credentials)

    # Event ID must be base32hex: lowercase a-v + 0-9 only
    event_id = f"demoappt{start_dt.strftime('%Y%m%d')}"

    event_body = {
        "id": event_id,
        "summary": f"{DEMO_SERVICE} — {DEMO_CUSTOMER_NAME}",
        "description": (
            f"Customer: {DEMO_CUSTOMER_NAME}\n"
            f"Phone: {DEMO_CUSTOMER_PHONE}\n"
            f"Service: {DEMO_SERVICE}\n\n"
            "(Hard-coded demo appointment for Alex's Plumbing Service)"
        ),
        "location": "Alex's Plumbing Service",
        "start": {"dateTime": start_dt.isoformat(), "timeZone": TIMEZONE},
        "end":   {"dateTime": end_dt.isoformat(),   "timeZone": TIMEZONE},
    }
    resp = session.post(
        f"https://www.googleapis.com/calendar/v3/calendars/{settings.GOOGLE_CALENDAR_ID}/events",
        json=event_body,
    )
    if resp.status_code == 409:
        logger.info("Google Calendar event %s already exists — skipping.", event_id)
        return
    resp.raise_for_status()
    logger.info(
        "Created Google Calendar event %s for %s",
        event_id, start_dt.isoformat(),
    )
