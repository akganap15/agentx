"""Agent system prompts package."""

from backend.src.agents.prompts.orchestrator import ORCHESTRATOR_SYSTEM_PROMPT
from backend.src.agents.prompts.lead_catcher import LEAD_CATCHER_SYSTEM_PROMPT
from backend.src.agents.prompts.review_pilot import REVIEW_PILOT_SYSTEM_PROMPT
from backend.src.agents.prompts.after_hours import AFTER_HOURS_SYSTEM_PROMPT
from backend.src.agents.prompts.booking_boss import BOOKING_BOSS_SYSTEM_PROMPT
from backend.src.agents.prompts.campaign import CAMPAIGN_SYSTEM_PROMPT

__all__ = [
    "ORCHESTRATOR_SYSTEM_PROMPT",
    "LEAD_CATCHER_SYSTEM_PROMPT",
    "REVIEW_PILOT_SYSTEM_PROMPT",
    "AFTER_HOURS_SYSTEM_PROMPT",
    "BOOKING_BOSS_SYSTEM_PROMPT",
    "CAMPAIGN_SYSTEM_PROMPT",
]
