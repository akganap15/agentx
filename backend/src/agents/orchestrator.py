"""
Main Orchestrator Agent.

Responsibilities:
  1. Accept an InboundEvent
  2. Call Claude to classify intent (lead, review, after_hours, booking, campaign)
  3. Route to the appropriate specialist agent
  4. Return the specialist's response dict

Uses the Anthropic Python SDK with a lightweight classification call (no tools)
to keep latency low, then delegates the full agentic loop to the specialist.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from backend.src.config import settings
from backend.src.models.event import InboundEvent
from backend.src.agents.prompts.orchestrator import (
    ORCHESTRATOR_SYSTEM_PROMPT,
    build_orchestrator_user_prompt,
)
from backend.src.agents.litellm_client import litellm_classify

logger = logging.getLogger(__name__)

# Map classification labels to agent module imports (lazy to avoid circular deps)
AGENT_MAP = {
    "lead_catcher":  "backend.src.agents.lead_catcher.LeadCatcherAgent",
    "review_pilot":  "backend.src.agents.review_pilot.ReviewPilotAgent",
    "after_hours":   "backend.src.agents.after_hours.AfterHoursAgent",
    "booking_boss":  "backend.src.agents.booking_boss.BookingBossAgent",
    "campaign":      "backend.src.agents.campaign.CampaignAgent",
}


class Orchestrator:
    """
    Routes inbound events to the appropriate specialist agent.

    Usage:
        orchestrator = Orchestrator(store=app.state.store)
        result = await orchestrator.handle(event)
    """

    def __init__(self, store: Any = None) -> None:
        self.store = store

    async def handle(self, event: InboundEvent, history: Optional[list] = None) -> Dict[str, Any]:
        """
        Main entry point. Classifies the event and delegates to a specialist.

        Returns a dict with keys:
          - agent: str — which specialist handled it
          - reply: str — the message to send back to the customer
          - outcome: str — terminal outcome label
          - routing: dict — the raw classification from Claude
        """
        logger.info("Orchestrator handling event_id=%s source=%s", event.id, event.source)

        # 1. Load business context
        business = None
        if self.store:
            business = await self.store.get_business(event.business_id)

        business_dict = business.model_dump() if business else {"name": "the business", "id": event.business_id}

        # Enrich with pre-formatted summaries so every agent prompt has context
        business_dict["hours_summary"] = self._format_hours(business_dict.get("hours", {}))
        business_dict["services_summary"] = self._format_services(business_dict.get("services", []))
        business_dict["next_open"] = self._next_open_label(
            business_dict.get("hours", {}), business_dict.get("timezone", "America/New_York")
        )

        # 2. Load customer history
        customer_history = "No prior contact."
        if self.store and event.from_number:
            customer = await self.store.get_customer(event.from_number)
            if customer:
                customer_history = (
                    f"Name: {customer.name or 'Unknown'}, "
                    f"Visits: {customer.total_visits}, "
                    f"Lead stage: {customer.lead_stage}, "
                    f"Notes: {customer.notes or 'none'}"
                )

        # 3. Determine if after-hours
        is_after_hours = self._is_after_hours(business_dict)

        # 4. Classify intent via Claude
        routing = await self._classify(
            message=event.message_body or "",
            business=business_dict,
            is_after_hours=is_after_hours,
            customer_history=customer_history,
        )

        agent_name = routing.get("agent", "after_hours")
        logger.info(
            "Classified event_id=%s as agent=%s confidence=%.2f urgency=%s",
            event.id,
            agent_name,
            routing.get("confidence", 0.0),
            routing.get("urgency", "medium"),
        )

        # 5. Delegate to specialist agent
        agent_instance = self._load_agent(agent_name)
        result = await agent_instance.run(
            event=event,
            business=business_dict,
            store=self.store,
            history=history or [],
        )

        result["agent"] = agent_name
        result["routing"] = routing
        return result

    async def _classify(
        self,
        message: str,
        business: Dict[str, Any],
        is_after_hours: bool,
        customer_history: str,
    ) -> Dict[str, Any]:
        """
        Classify the inbound message via LiteLLM.
        Returns the parsed JSON routing decision.
        """
        hours_summary = self._format_hours(business.get("hours", {}))
        user_prompt = build_orchestrator_user_prompt(
            message=message,
            business_name=business.get("name", "the business"),
            business_hours_summary=hours_summary,
            is_after_hours=is_after_hours,
            customer_history=customer_history,
        )

        raw_text = await litellm_classify(
            system=ORCHESTRATOR_SYSTEM_PROMPT,
            user_message=user_prompt,
            max_tokens=150,
        )
        logger.debug("Orchestrator raw classification: %s", raw_text)

        try:
            # Extract JSON even if wrapped in markdown code fences
            if "```" in raw_text:
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
            return json.loads(raw_text)
        except (json.JSONDecodeError, IndexError) as exc:
            logger.warning("Failed to parse orchestrator JSON: %s — raw: %s", exc, raw_text)
            return {
                "agent": "after_hours",
                "confidence": 0.0,
                "intent_summary": "Classification failed — defaulting to after_hours",
                "urgency": "medium",
                "is_after_hours": is_after_hours,
            }

    def _load_agent(self, agent_name: str) -> Any:
        """Dynamically import and instantiate a specialist agent."""
        import importlib

        dotted = AGENT_MAP.get(agent_name)
        if not dotted:
            logger.warning("Unknown agent '%s', falling back to after_hours", agent_name)
            dotted = AGENT_MAP["after_hours"]

        module_path, class_name = dotted.rsplit(".", 1)
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        return cls()

    def _is_after_hours(self, business: Dict[str, Any]) -> bool:
        """
        Check whether the current time falls outside business operating hours.
        Uses the business timezone and hours dict.
        """
        hours = business.get("hours", {})
        if not hours:
            return False  # no hours configured → assume always open

        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(business.get("timezone", "America/Chicago"))
            now = datetime.now(tz)
        except Exception:
            now = datetime.utcnow()

        day_name = now.strftime("%A").lower()  # e.g. "monday"
        day_hours = hours.get(day_name)
        if not day_hours:
            return True  # not configured → treat as closed

        # Handle both dict and BusinessHours model
        if hasattr(day_hours, "closed"):
            if day_hours.closed:
                return True
            open_t = day_hours.open
            close_t = day_hours.close
        else:
            if day_hours.get("closed", False):
                return True
            open_t = day_hours.get("open", "09:00")
            close_t = day_hours.get("close", "17:00")

        open_h, open_m = map(int, open_t.split(":"))
        close_h, close_m = map(int, close_t.split(":"))

        open_minutes = open_h * 60 + open_m
        close_minutes = close_h * 60 + close_m
        current_minutes = now.hour * 60 + now.minute

        return not (open_minutes <= current_minutes < close_minutes)

    def _format_services(self, services: list) -> str:
        """Return a comma-separated list of service names for agent prompts."""
        if not services:
            return "General services"
        names = [s.get("name", "") if isinstance(s, dict) else getattr(s, "name", "") for s in services]
        names = [n for n in names if n]
        return ", ".join(names) if names else "General services"

    def _next_open_label(self, hours: Dict[str, Any], timezone: str) -> str:
        """Return a human-readable next opening time, e.g. 'Monday at 8:00 AM'."""
        if not hours:
            return "tomorrow morning"
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(timezone)
            now = datetime.now(tz)
        except Exception:
            now = datetime.utcnow()
        day_names = ["sunday","monday","tuesday","wednesday","thursday","friday","saturday"]
        for offset in range(1, 8):
            candidate = (now.weekday() + offset + 1) % 7  # weekday() is Mon=0; day_names is Sun=0
            day_name = day_names[(now.weekday() + offset + 1) % 7]
            h = hours.get(day_name)
            if not h:
                continue
            closed = h.get("closed", False) if isinstance(h, dict) else getattr(h, "closed", False)
            if not closed:
                open_t = h.get("open", "09:00") if isinstance(h, dict) else getattr(h, "open", "09:00")
                label = day_name.capitalize() if offset > 1 else "tomorrow"
                return f"{label} at {open_t}"
        return "when we reopen"

    def _format_hours(self, hours: Dict[str, Any]) -> str:
        if not hours:
            return "Not specified"
        lines = []
        for day, h in hours.items():
            if hasattr(h, "closed"):
                if h.closed:
                    lines.append(f"{day.capitalize()}: Closed")
                else:
                    lines.append(f"{day.capitalize()}: {h.open}–{h.close}")
            elif isinstance(h, dict):
                if h.get("closed"):
                    lines.append(f"{day.capitalize()}: Closed")
                else:
                    lines.append(f"{day.capitalize()}: {h.get('open', '?')}–{h.get('close', '?')}")
        return ", ".join(lines)
