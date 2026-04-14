"""
Google Calendar Tool.

Provides availability checking and appointment booking via the Google Calendar API.
In demo mode (no service-account key), returns realistic mock data so the agents
still function.

This module is business-aware: slots are generated in America/Los_Angeles respecting
Alex's Plumbing Service hours (Mon–Fri 8–18, Sat 9–14, closed Sunday).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from backend.src.config import settings

logger = logging.getLogger(__name__)

# Business timezone — all slot generation, bookings, and freeBusy windows use this.
SALON_TZ = ZoneInfo("America/Los_Angeles")

# Working hours keyed by weekday() (0=Mon, 6=Sun). None = closed.
# Values are (open_hour, close_hour) in 24h local time.
SALON_HOURS: dict[int, Optional[Tuple[int, int]]] = {
    0: (8, 18),      # Monday
    1: (8, 18),      # Tuesday
    2: (8, 18),      # Wednesday
    3: (8, 18),      # Thursday
    4: (8, 18),      # Friday
    5: (9, 14),      # Saturday
    6: None,         # Sunday — closed
}

SALON_LOCATION = "Alex's Plumbing Service"


class CalendarTool:
    """
    Google Calendar integration for appointment scheduling.

    Authenticates via a service account JSON key and talks to the Google
    Calendar API v3 over REST (no httplib2).
    """

    def __init__(self) -> None:
        self._service = None  # lazy-initialized AuthorizedSession

    def _get_session(self):
        """Return a lazily-created AuthorizedSession (requests-based, no httplib2)."""
        if self._service:
            return self._service

        if not settings.GOOGLE_SERVICE_ACCOUNT_JSON:
            raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON not configured.")

        from google.oauth2 import service_account  # type: ignore
        from google.auth.transport.requests import AuthorizedSession  # type: ignore

        scopes = ["https://www.googleapis.com/auth/calendar"]
        credentials = service_account.Credentials.from_service_account_file(
            settings.GOOGLE_SERVICE_ACCOUNT_JSON,
            scopes=scopes,
        )
        self._service = AuthorizedSession(credentials)
        return self._service

    # ------------------------------------------------------------------ #
    # Availability
    # ------------------------------------------------------------------ #

    async def get_availability(
        self,
        business_id: str,
        duration_minutes: int = 60,
        preferred_days: Optional[List[str]] = None,
        days_ahead: int = 7,
    ) -> List[str]:
        """
        Return a list of open appointment slots as ISO 8601 local datetime strings
        (with Pacific offset) for the next `days_ahead` days.

        Production: Google Calendar FreeBusy API.
        Demo mode (no key): synthetic slots that still respect business hours.
        """
        if not settings.GOOGLE_SERVICE_ACCOUNT_JSON:
            return self._demo_availability(duration_minutes, days_ahead)

        try:
            return await self._real_availability(duration_minutes, days_ahead)
        except Exception as exc:
            logger.warning("Google Calendar availability failed, using demo data: %s", exc)
            return self._demo_availability(duration_minutes, days_ahead)

    async def _real_availability(
        self, duration_minutes: int, days_ahead: int
    ) -> List[str]:
        """Query Google Calendar FreeBusy API for open slots within business hours."""
        session = self._get_session()

        now_local = datetime.now(SALON_TZ)
        end_local = now_local + timedelta(days=days_ahead)

        # FreeBusy expects RFC3339 — send UTC, Google handles TZ comparisons server-side.
        resp = session.post(
            "https://www.googleapis.com/calendar/v3/freeBusy",
            json={
                "timeMin": now_local.astimezone(timezone.utc).isoformat(),
                "timeMax": end_local.astimezone(timezone.utc).isoformat(),
                "timeZone": str(SALON_TZ),
                "items": [{"id": settings.GOOGLE_CALENDAR_ID}],
            },
        )
        resp.raise_for_status()
        busy_raw = (
            resp.json()
            .get("calendars", {})
            .get(settings.GOOGLE_CALENDAR_ID, {})
            .get("busy", [])
        )
        # Parse once into tz-aware datetimes for fast comparison below.
        busy_periods: List[Tuple[datetime, datetime]] = []
        for b in busy_raw:
            try:
                b_start = datetime.fromisoformat(b["start"].replace("Z", "+00:00"))
                b_end = datetime.fromisoformat(b["end"].replace("Z", "+00:00"))
                busy_periods.append((b_start, b_end))
            except (KeyError, ValueError):
                continue

        return self._generate_slots(
            start=now_local,
            duration_minutes=duration_minutes,
            days_ahead=days_ahead,
            busy_periods=busy_periods,
        )

    def _demo_availability(
        self, duration_minutes: int, days_ahead: int
    ) -> List[str]:
        """Synthetic slots when no Google Calendar key is available."""
        return self._generate_slots(
            start=datetime.now(SALON_TZ),
            duration_minutes=duration_minutes,
            days_ahead=days_ahead,
            busy_periods=[],
        )

    def _generate_slots(
        self,
        start: datetime,
        duration_minutes: int,
        days_ahead: int,
        busy_periods: List[Tuple[datetime, datetime]],
        max_slots: int = 8,
    ) -> List[str]:
        """
        Walk forward from `start` in 30-minute steps, returning up to `max_slots`
        slots that (a) fall inside business hours for that weekday and (b) don't
        overlap any busy period.
        """
        # Round UP to the next 30-min boundary so we don't offer a time that's
        # already partially in the past.
        minute_bucket = 30 if start.minute < 30 else 60
        cursor = start.replace(
            minute=(minute_bucket % 60), second=0, microsecond=0
        )
        if minute_bucket == 60:
            cursor += timedelta(hours=1)

        end_window = start + timedelta(days=days_ahead)
        slots: List[str] = []

        while cursor < end_window and len(slots) < max_slots:
            hours = SALON_HOURS.get(cursor.weekday())
            if hours is None:
                # Closed today — jump to the start of the next day.
                cursor = (cursor + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                continue

            open_h, close_h = hours
            if cursor.hour < open_h:
                cursor = cursor.replace(hour=open_h, minute=0)
                continue

            slot_finish = cursor + timedelta(minutes=duration_minutes)
            if slot_finish.hour > close_h or (
                slot_finish.hour == close_h and slot_finish.minute > 0
            ):
                # Appointment would run past closing — jump to next day.
                cursor = (cursor + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                continue

            is_free = not any(
                b_start < slot_finish and b_end > cursor
                for b_start, b_end in busy_periods
            )
            if is_free:
                slots.append(cursor.isoformat())

            cursor += timedelta(minutes=30)

        return slots

    # ------------------------------------------------------------------ #
    # Booking
    # ------------------------------------------------------------------ #

    async def book_appointment(
        self,
        business_id: str,
        customer_phone: str,
        customer_name: str,
        service: str,
        appointment_dt: str,
        duration_minutes: int = 60,
        notes: str = "",
    ) -> str:
        """
        Create a Google Calendar event for the appointment. Returns the created
        event ID. Raises on real API failures — the caller decides whether to
        apologize or retry; this function no longer silently returns a fake ID.
        """
        if not settings.GOOGLE_SERVICE_ACCOUNT_JSON:
            demo_id = f"demo-evt-{datetime.now(SALON_TZ).timestamp():.0f}"
            logger.info(
                "[DEMO] Booked appointment: %s at %s (%d min)",
                service, appointment_dt, duration_minutes,
            )
            return demo_id

        session = self._get_session()

        start_dt = datetime.fromisoformat(appointment_dt)
        # If the caller passed a naive ISO string, assume it's local time.
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=SALON_TZ)
        end_dt = start_dt + timedelta(minutes=duration_minutes)

        event_body = {
            "summary": f"{service} — {customer_name}",
            "description": (
                f"Customer: {customer_name}\n"
                f"Phone: {customer_phone}\n"
                f"Service: {service}\n"
                f"Notes: {notes}"
            ),
            "location": SALON_LOCATION,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": str(SALON_TZ)},
            "end":   {"dateTime": end_dt.isoformat(),   "timeZone": str(SALON_TZ)},
            "reminders": {
                "useDefault": False,
                "overrides": [{"method": "email", "minutes": 24 * 60}],
            },
        }
        resp = session.post(
            f"https://www.googleapis.com/calendar/v3/calendars/{settings.GOOGLE_CALENDAR_ID}/events",
            json=event_body,
        )
        resp.raise_for_status()
        event_id = resp.json()["id"]
        logger.info(
            "Booked Google Calendar event %s: %s for %s at %s (%d min)",
            event_id, service, customer_name, start_dt.isoformat(), duration_minutes,
        )
        return event_id

    # ------------------------------------------------------------------ #
    # Cancellation
    # ------------------------------------------------------------------ #

    async def cancel_event(self, event_id: str) -> bool:
        """Delete a Google Calendar event by its event ID."""
        if not settings.GOOGLE_SERVICE_ACCOUNT_JSON:
            logger.info("[DEMO] Cancelled calendar event: %s", event_id)
            return True
        try:
            session = self._get_session()
            resp = session.delete(
                f"https://www.googleapis.com/calendar/v3/calendars/{settings.GOOGLE_CALENDAR_ID}/events/{event_id}"
            )
            resp.raise_for_status()
            logger.info("Deleted Google Calendar event: %s", event_id)
            return True
        except Exception as exc:
            logger.exception("Google Calendar cancel failed for %s: %s", event_id, exc)
            return False
