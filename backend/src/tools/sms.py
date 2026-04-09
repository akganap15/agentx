"""
SMS Tool — T-Mobile network-native SMS via T-Mobile API with Twilio fallback.

Priority:
  1. T-Mobile network API (lower latency, carrier-native for T-Mobile subscribers)
  2. Twilio (fallback for non-T-Mobile numbers or when T-Mobile API unavailable)

In demo mode (no API keys configured) all sends are logged and return a mock SID.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

from backend.src.config import settings

logger = logging.getLogger(__name__)


class SMSTool:
    """
    Unified SMS sender.

    Usage:
        tool = SMSTool()
        result = await tool.send(to="+15551234567", body="Hi from Pete's Plumbing!")
    """

    async def send(
        self,
        to: str,
        body: str,
        from_number: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send an SMS message.

        Returns:
            {"success": True, "sid": "<message_sid>", "provider": "tmobile|twilio|demo"}
        """
        # Prefer T-Mobile network API when configured
        if settings.TMOBILE_SMS_API_KEY:
            try:
                return await self._send_tmobile(to=to, body=body, from_number=from_number)
            except Exception as exc:
                logger.warning("T-Mobile SMS failed, falling back to Twilio: %s", exc)

        # Twilio fallback
        if settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN:
            try:
                return await self._send_twilio(to=to, body=body, from_number=from_number)
            except Exception as exc:
                logger.warning("Twilio SMS failed: %s", exc)

        # Demo mode: log and return mock
        logger.info("[DEMO SMS] To: %s | Body: %s", to, body[:80])
        return {"success": True, "sid": "DEMO_SMS_SID", "provider": "demo"}

    async def _send_tmobile(
        self, to: str, body: str, from_number: Optional[str]
    ) -> Dict[str, Any]:
        """Send via T-Mobile network-native SMS API."""
        headers = {
            "Authorization": f"Bearer {settings.TMOBILE_SMS_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "to": to,
            "from": from_number or settings.TWILIO_PHONE_NUMBER,
            "body": body,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{settings.TMOBILE_SMS_API_URL}/messages",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            return {
                "success": True,
                "sid": data.get("messageId", "unknown"),
                "provider": "tmobile",
            }

    async def _send_twilio(
        self, to: str, body: str, from_number: Optional[str]
    ) -> Dict[str, Any]:
        """Send via Twilio REST API."""
        from twilio.rest import Client  # type: ignore
        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            to=to,
            from_=from_number or settings.TWILIO_PHONE_NUMBER,
            body=body,
        )
        logger.info("Twilio SMS sent: SID=%s status=%s", message.sid, message.status)
        return {"success": True, "sid": message.sid, "provider": "twilio"}

    async def receive_webhook(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse an inbound Twilio/T-Mobile SMS webhook payload.
        Returns a normalised dict ready for InboundEvent construction.
        """
        return {
            "from_number": payload.get("From", payload.get("from", "")),
            "to_number": payload.get("To", payload.get("to", "")),
            "body": payload.get("Body", payload.get("body", "")),
            "message_sid": payload.get("MessageSid", payload.get("messageId", "")),
        }
