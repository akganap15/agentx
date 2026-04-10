"""
Google Calendar Tool.

Provides availability checking and appointment booking via the Google Calendar API.
In demo mode (no API key), returns realistic mock data so the agents still function.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from backend.src.config import settings

logger = logging.getLogger(__name__)

# Working hours for slot generation (demo mode)
DEMO_WORK_HOURS = [(9, 0), (10, 0), (11, 0), (13, 0), (14, 0), (15, 0), (16, 0)]


class CalendarTool:
    """
    Google Calendar integration for appointment scheduling.

    In production, authenticates via a service account JSON key and
    interacts with the Google Calendar API v3.
    """

    def __init__(self) -> None:
        self._service = None  # lazy-initialized Google API service

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

    async def get_availability(
        self,
        business_id: str,
        duration_minutes: int = 60,
        preferred_days: Optional[List[str]] = None,
        days_ahead: int = 7,
    ) -> List[str]:
        """
        Return a list of available ISO 8601 datetime strings for the next `days_ahead` days.

        In production: calls Google Calendar FreeBusy API to find gaps.
        In demo mode: returns synthetic available slots.
        """
        if not settings.GOOGLE_SERVICE_ACCOUNT_JSON:
            return self._demo_availability(duration_minutes, days_ahead)

        try:
            return await self._real_availability(duration_minutes, days_ahead)
        except Exception as exc:
            logger.warning("Google Calendar availability failed, using demo data: %s", exc)
            return self._demo_availability(duration_minutes, days_ahead)

    async def _real_availability(self, duration_minutes: int, days_ahead: int) -> List[str]:
        """Query Google Calendar FreeBusy API via REST for open slots."""
        session = self._get_session()
        now = datetime.utcnow()
        end = now + timedelta(days=days_ahead)

        resp = session.post(
            "https://www.googleapis.com/calendar/v3/freeBusy",
            json={
                "timeMin": now.isoformat() + "Z",
                "timeMax": end.isoformat() + "Z",
                "items": [{"id": settings.GOOGLE_CALENDAR_ID}],
            },
        )
        resp.raise_for_status()
        busy_periods = resp.json().get("calendars", {}).get(settings.GOOGLE_CALENDAR_ID, {}).get("busy", [])

        slots = []
        current = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        slot_end = current + timedelta(days=days_ahead)

        while current < slot_end and len(slots) < 8:
            if current.hour < 9 or current.hour >= 17:
                current += timedelta(hours=1)
                continue
            if current.weekday() >= 6:
                current += timedelta(days=1)
                continue

            slot_finish = current + timedelta(minutes=duration_minutes)
            is_free = not any(
                datetime.fromisoformat(b["start"].replace("Z", "")) <= current
                and datetime.fromisoformat(b["end"].replace("Z", "")) >= slot_finish
                for b in busy_periods
            )
            if is_free:
                slots.append(current.isoformat())
            current += timedelta(minutes=30)

        return slots

    def _demo_availability(self, duration_minutes: int, days_ahead: int) -> List[str]:
        """Return realistic demo slots when no Google API key is available."""
        slots = []
        base = datetime.utcnow().replace(minute=0, second=0, microsecond=0) + timedelta(hours=2)
        for day_offset in range(1, days_ahead + 1):
            day = base + timedelta(days=day_offset)
            if day.weekday() >= 6:  # Skip Sunday
                continue
            for hour, minute in DEMO_WORK_HOURS[:3]:
                slot = day.replace(hour=hour, minute=minute)
                slots.append(slot.isoformat())
        return slots[:8]

    async def book_appointment(
        self,
        business_id: str,
        customer_phone: str,
        customer_name: str,
        service: str,
        appointment_dt: str,
        notes: str = "",
    ) -> str:
        """
        Create a Google Calendar event for the appointment.

        Returns the created event ID (or a demo ID).
        """
        if not settings.GOOGLE_SERVICE_ACCOUNT_JSON:
            demo_id = f"demo-evt-{datetime.utcnow().timestamp():.0f}"
            logger.info("[DEMO] Booked appointment: %s at %s", service, appointment_dt)
            return demo_id

        try:
            session = self._get_session()
            start_dt = datetime.fromisoformat(appointment_dt)
            end_dt = start_dt + timedelta(minutes=90)

            event_body = {
                "summary": f"{service} — {customer_name}",
                "description": f"Customer: {customer_name}\nPhone: {customer_phone}\nNotes: {notes}",
                "start": {"dateTime": start_dt.isoformat() + "Z", "timeZone": "UTC"},
                "end": {"dateTime": end_dt.isoformat() + "Z", "timeZone": "UTC"},
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
            return resp.json()["id"]

        except Exception as exc:
            logger.exception("Google Calendar booking failed: %s", exc)
            return f"demo-evt-{datetime.utcnow().timestamp():.0f}"

    async def cancel_event(self, event_id: str) -> bool:
        """
        Delete a Google Calendar event by its event ID.
        Returns True on success. Falls back gracefully in demo mode.
        """
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
