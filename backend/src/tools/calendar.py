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

    def _get_service(self) -> Any:
        """Lazy-initialize the Google Calendar API service."""
        if self._service:
            return self._service

        if not settings.GOOGLE_CALENDAR_API_KEY:
            raise RuntimeError("GOOGLE_CALENDAR_API_KEY not configured.")

        from googleapiclient.discovery import build  # type: ignore
        from google.oauth2 import service_account  # type: ignore

        # In production, load service account credentials from a JSON file
        # For simplicity here, we use an API key (read-only) or SA credentials
        self._service = build("calendar", "v3", developerKey=settings.GOOGLE_CALENDAR_API_KEY)
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
        if not settings.GOOGLE_CALENDAR_API_KEY:
            return self._demo_availability(duration_minutes, days_ahead)

        try:
            return await self._real_availability(duration_minutes, days_ahead)
        except Exception as exc:
            logger.warning("Google Calendar availability failed, using demo data: %s", exc)
            return self._demo_availability(duration_minutes, days_ahead)

    async def _real_availability(self, duration_minutes: int, days_ahead: int) -> List[str]:
        """Query Google Calendar FreeBusy for open slots."""
        service = self._get_service()
        now = datetime.utcnow()
        end = now + timedelta(days=days_ahead)

        body = {
            "timeMin": now.isoformat() + "Z",
            "timeMax": end.isoformat() + "Z",
            "items": [{"id": settings.GOOGLE_CALENDAR_ID}],
        }
        result = service.freebusy().query(body=body).execute()
        busy_periods = result.get("calendars", {}).get(settings.GOOGLE_CALENDAR_ID, {}).get("busy", [])

        # Find free slots by walking the day
        slots = []
        current = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        slot_end = current + timedelta(days=days_ahead)

        while current < slot_end and len(slots) < 8:
            # Only suggest working hours
            if current.hour < 9 or current.hour >= 17:
                current += timedelta(hours=1)
                continue
            if current.weekday() >= 6:  # Sunday
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
        if not settings.GOOGLE_CALENDAR_API_KEY:
            demo_id = f"demo-evt-{datetime.utcnow().timestamp():.0f}"
            logger.info("[DEMO] Booked appointment: %s at %s", service, appointment_dt)
            return demo_id

        try:
            service = self._get_service()
            start_dt = datetime.fromisoformat(appointment_dt)
            end_dt = start_dt + timedelta(minutes=90)

            event_body = {
                "summary": f"{service} — {customer_name}",
                "description": f"Customer: {customer_name}\nPhone: {customer_phone}\nNotes: {notes}",
                "start": {"dateTime": start_dt.isoformat(), "timeZone": "UTC"},
                "end": {"dateTime": end_dt.isoformat(), "timeZone": "UTC"},
                "reminders": {
                    "useDefault": False,
                    "overrides": [
                        {"method": "sms", "minutes": 2 * 60},  # 2hr reminder
                        {"method": "sms", "minutes": 24 * 60},  # 24hr reminder
                    ],
                },
            }
            created = service.events().insert(
                calendarId=settings.GOOGLE_CALENDAR_ID, body=event_body
            ).execute()
            return created["id"]

        except Exception as exc:
            logger.exception("Google Calendar booking failed: %s", exc)
            return f"demo-evt-{datetime.utcnow().timestamp():.0f}"
