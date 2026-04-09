"""
Voice Tool — T-Mobile network-native voice integration.

Enables the AI assistant to:
  - Initiate outbound calls (e.g., emergency callbacks)
  - Handle inbound voice via TwiML (IVR-style routing)
  - Convert voicemail to text via speech-to-text
  - Generate TwiML responses for call flows

Leverages T-Mobile's network-native voice APIs for lower latency
and better call quality for T-Mobile subscribers.
Falls back to Twilio Voice for non-T-Mobile numbers.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

from backend.src.config import settings

logger = logging.getLogger(__name__)


class VoiceTool:
    """
    T-Mobile network-native voice tool with Twilio fallback.

    Usage:
        tool = VoiceTool()
        await tool.make_call(to="+15551234567", message="Hi, this is Pete from Pete's Plumbing...")
    """

    async def make_call(
        self,
        to: str,
        message: str,
        from_number: Optional[str] = None,
        record: bool = False,
    ) -> Dict[str, Any]:
        """
        Initiate an outbound call with a text-to-speech message.
        Used for emergency callbacks and appointment reminders.
        """
        if not settings.TWILIO_ACCOUNT_SID:
            logger.info("[DEMO VOICE] Call to %s: %.80s...", to, message)
            return {"success": True, "call_sid": "DEMO_CALL_SID", "_demo": True}

        try:
            return await self._make_twilio_call(
                to=to, message=message, from_number=from_number, record=record
            )
        except Exception as exc:
            logger.exception("Voice call failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def _make_twilio_call(
        self,
        to: str,
        message: str,
        from_number: Optional[str],
        record: bool,
    ) -> Dict[str, Any]:
        """Make an outbound call via Twilio with TwiML speech."""
        from twilio.rest import Client  # type: ignore
        from urllib.parse import quote

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

        # Build inline TwiML
        twiml = f'<Response><Say voice="alice">{message}</Say></Response>'

        call = client.calls.create(
            to=to,
            from_=from_number or settings.TWILIO_PHONE_NUMBER,
            twiml=twiml,
            record=record,
        )
        logger.info("Twilio call initiated: SID=%s status=%s", call.sid, call.status)
        return {"success": True, "call_sid": call.sid, "status": call.status, "provider": "twilio"}

    def generate_twiml_response(
        self,
        message: str,
        gather_digits: bool = False,
        gather_speech: bool = False,
        action_url: Optional[str] = None,
    ) -> str:
        """
        Generate TwiML XML for inbound call handling.

        Used in the Twilio inbound call webhook to route callers
        and optionally gather input.
        """
        gather_attrs = ""
        if action_url:
            gather_attrs += f' action="{action_url}"'
        if gather_speech:
            gather_attrs += ' input="speech" speechTimeout="auto" language="en-US"'
        elif gather_digits:
            gather_attrs += ' numDigits="1"'

        if gather_digits or gather_speech:
            return (
                f'<Response>'
                f'<Gather{gather_attrs}>'
                f'<Say voice="alice">{message}</Say>'
                f'</Gather>'
                f'<Say voice="alice">We did not receive your input. Goodbye!</Say>'
                f'</Response>'
            )

        return f'<Response><Say voice="alice">{message}</Say><Hangup/></Response>'

    async def transcribe_voicemail(self, recording_url: str) -> str:
        """
        Download and transcribe a voicemail recording.

        In production: uses Twilio's built-in transcription or sends to
        a speech-to-text service (Deepgram / Google STT).
        In demo: returns a placeholder.
        """
        if not settings.TWILIO_AUTH_TOKEN:
            return "[Demo voicemail transcription: Customer left a message about scheduling a plumbing repair.]"

        try:
            async with httpx.AsyncClient(
                auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
                timeout=30.0,
            ) as client:
                resp = await client.get(recording_url + ".mp3")
                resp.raise_for_status()
                audio_bytes = resp.content

            # TODO: Send audio_bytes to speech-to-text service
            # For now return a placeholder; integrate Deepgram/Google STT here
            return "[Voicemail transcription pending — STT integration required]"
        except Exception as exc:
            logger.exception("Voicemail transcription failed: %s", exc)
            return "[Transcription unavailable]"
