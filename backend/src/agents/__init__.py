"""
Agents package.

Each agent is a specialist that handles one dimension of business communication:
  - Orchestrator:  Classifies intent and routes to the right specialist
  - LeadCatcher:   Qualifies inbound leads and books appointments
  - ReviewPilot:   Responds to reviews and solicits new ones
  - AfterHours:    24/7 FAQ and emergency reception
  - BookingBoss:   No-show follow-up and waitlist management
  - Campaign:      Win-back and re-engagement SMS/email campaigns

All agents use the Anthropic Python SDK with tool_use for structured actions.
"""

from backend.src.agents.orchestrator import Orchestrator
from backend.src.agents.lead_catcher import LeadCatcherAgent
from backend.src.agents.review_pilot import ReviewPilotAgent
from backend.src.agents.after_hours import AfterHoursAgent
from backend.src.agents.booking_boss import BookingBossAgent
from backend.src.agents.campaign import CampaignAgent

__all__ = [
    "Orchestrator",
    "LeadCatcherAgent",
    "ReviewPilotAgent",
    "AfterHoursAgent",
    "BookingBossAgent",
    "CampaignAgent",
]
