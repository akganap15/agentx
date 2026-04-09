"""
Tools package — external integrations used by AI agents.

Each tool wraps a third-party API (Twilio, Google, SendGrid) and
provides a clean async interface that agents can call via tool_use.
All tools are designed to degrade gracefully in demo mode.
"""

from backend.src.tools.sms import SMSTool
from backend.src.tools.calendar import CalendarTool
from backend.src.tools.reviews import ReviewsTool
from backend.src.tools.email import EmailTool
from backend.src.tools.voice import VoiceTool

__all__ = ["SMSTool", "CalendarTool", "ReviewsTool", "EmailTool", "VoiceTool"]
