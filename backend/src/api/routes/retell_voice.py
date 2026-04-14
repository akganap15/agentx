"""
Retell AI voice routes.

Two endpoints:
  POST /api/v1/voice/retell/register-call
    - Frontend calls this to start a Retell web call.
    - Backend calls Retell's REST API and returns an access_token to the browser.

  WS /api/v1/voice/retell/llm-webhook/{call_id}
    - Retell opens a WebSocket for each call (custom-LLM pattern).
    - Each turn, we run a short tool-use loop against Claude via LiteLLM so the
      agent can check availability and create real Google Calendar events.

Setup (one-time, manual):
  1. Sign up at app.retellai.com
  2. Create a Custom LLM agent — set the Custom LLM URL to:
       wss://<your-public-url>/api/v1/voice/retell/llm-webhook
  3. Copy the Agent ID → RETELL_AGENT_ID in .env
  4. Copy the API key  → RETELL_API_KEY in .env
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from backend.src.agents.litellm_client import litellm_chat
from backend.src.config import settings
from backend.src.models.conversation import (
    Conversation,
    ConversationMessage,
    MessageRole,
)
from backend.src.tools.calendar import SALON_TZ, CalendarTool

# Voice-tuned system prompt — short, conversational, no markdown
RETELL_SYSTEM_PROMPT = (
    "You are a warm, friendly receptionist answering the phone for Alex's Plumbing Service. "
    "Talk like a real person — short, natural, conversational.\n\n"
    "VOICE STYLE:\n"
    "- Keep replies to 1 sentence, 2 max. Under 20 words when you can.\n"
    "- No markdown, no lists, no bullet points.\n"
    "- Ask one question at a time.\n"
    "- Don't overuse the customer's name — just talk naturally.\n\n"
    "SERVICES WE OFFER:\n"
    "- Pipe repair and replacement\n"
    "- Drain cleaning and unclogging\n"
    "- Water heater install and repair\n"
    "- Leak detection\n"
    "- Fixture installation (faucets, toilets, garbage disposals)\n"
    "- Emergency callouts (24/7)\n\n"
    "TYPICAL DURATIONS (use when calling tools):\n"
    "- Drain cleaning: 60 min.\n"
    "- Leak detection / diagnostic: 60 min.\n"
    "- Fixture install (faucet, toilet): 90 min.\n"
    "- Pipe repair: 120 min.\n"
    "- Water heater install: 180 min.\n\n"
    "WHEN SOMEONE CALLS TO BOOK:\n"
    "- Ask what the issue is and how urgent it is.\n"
    "- For emergencies, reassure them we can get someone out fast.\n"
    "- Call check_availability when the caller is ready to pick a time — pass a sensible\n"
    "  duration_minutes based on the service.\n"
    "- Offer a couple of specific times from the tool result rather than a wall of options.\n\n"
    "BOOKING DETAILS TO COLLECT BEFORE book_appointment:\n"
    "- Name, phone number, the service/issue, and the date/time.\n"
    "- Read it back to confirm. Only call book_appointment after you hear a clear yes.\n"
    "- When the caller says 'tomorrow' / 'Friday' / 'next Tuesday', resolve the actual\n"
    "  date yourself using TODAY'S DATE below, then pass ISO local time to the tool.\n\n"
    "WHEN SOMEONE CALLS WITH A QUESTION OR ISSUE:\n"
    "- Be warm and reassuring — 'No worries, I can help with that!'\n"
    "- For pricing, give a friendly ballpark and mention the final cost depends on the job.\n"
    "- For complaints about a recent service, apologize sincerely, take their details, "
    "and let them know a manager will follow up to make it right.\n\n"
    "HOURS:\n"
    "- Monday through Friday 8am to 6pm, Saturday 9am to 2pm, closed Sunday.\n"
    "- Emergency service available 24/7.\n"
    "- If they call outside regular hours for non-emergencies, let them know you're closed right now but "
    "you'd love to take their details and have someone call them back first thing when you reopen."
)

# Tools exposed to the voice agent (Anthropic tool_use format — litellm_client
# converts to OpenAI function-calling under the hood).
RETELL_VOICE_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "check_availability",
        "description": (
            "Check upcoming open appointment slots. "
            "Use when the caller is ready to pick a time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "duration_minutes": {
                    "type": "integer",
                    "description": (
                        "Expected appointment length. 60 for drain cleaning or leak detection, "
                        "90 for fixture install, 120 for pipe repair, 180 for water heater install."
                    ),
                },
                "days_ahead": {
                    "type": "integer",
                    "description": "How many days to search. Default 7.",
                },
            },
            "required": ["duration_minutes"],
        },
    },
    {
        "name": "book_appointment",
        "description": (
            "Create a real Google Calendar booking. Only call AFTER reading back "
            "name, phone, service, and time to the caller and hearing a clear yes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string"},
                "customer_phone": {
                    "type": "string",
                    "description": "E.164 format if possible, otherwise whatever the caller said.",
                },
                "service": {
                    "type": "string",
                    "description": "Short description, e.g. 'Pipe repair' or 'Water heater install'.",
                },
                "appointment_datetime": {
                    "type": "string",
                    "description": (
                        "ISO 8601 local (Pacific) time, e.g. 2026-04-12T14:00:00. "
                        "Resolve relative dates like 'tomorrow' yourself first."
                    ),
                },
                "duration_minutes": {"type": "integer"},
                "notes": {"type": "string"},
            },
            "required": [
                "customer_name",
                "customer_phone",
                "service",
                "appointment_datetime",
                "duration_minutes",
            ],
        },
    },
]

# Max Claude round-trips per Retell turn (1 = no tool use, 2 = one tool call + final reply).
MAX_TOOL_ITERATIONS = 3

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory session store keyed by Retell call_id.
# Each entry: {"history": [<anthropic messages>], "turns": int, "business_id": str}
_retell_sessions: Dict[str, Dict[str, Any]] = {}


# --------------------------------------------------------------------------- #
# REST: register a web call
# --------------------------------------------------------------------------- #

@router.post(
    "/register-call",
    summary="Create a Retell web call and return an access token to the browser",
)
async def register_call() -> JSONResponse:
    """
    Called by the frontend when the user clicks 'Simulate Call' in Retell mode.
    Calls Retell's REST API to create a web call and returns the access_token.
    """
    if not settings.RETELL_API_KEY or not settings.RETELL_AGENT_ID:
        return JSONResponse(
            {"error": "RETELL_API_KEY and RETELL_AGENT_ID must be set in .env"},
            status_code=503,
        )

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.retellai.com/v2/create-web-call",
            headers={
                "Authorization": f"Bearer {settings.RETELL_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"agent_id": settings.RETELL_AGENT_ID},
            timeout=10.0,
        )

    if resp.status_code != 201:
        logger.error("Retell register-call failed: %s %s", resp.status_code, resp.text)
        return JSONResponse(
            {"error": f"Retell API error: {resp.status_code}", "detail": resp.text},
            status_code=502,
        )

    data = resp.json()
    access_token = data.get("access_token")
    call_id = data.get("call_id")

    logger.info("Retell web call created: call_id=%s", call_id)

    _retell_sessions[call_id] = {
        "history": [],
        "turns": 0,
        "business_id": settings.DEMO_BUSINESS_ID,
    }

    return JSONResponse({"access_token": access_token, "call_id": call_id})


# --------------------------------------------------------------------------- #
# WebSocket: Retell custom-LLM turn handler
# --------------------------------------------------------------------------- #

@router.websocket("/llm-webhook/{call_id}")
async def llm_webhook(ws: WebSocket, call_id: str):
    """
    Retell Custom LLM WebSocket endpoint.

    Retell opens a WebSocket for each call. On each conversation turn it sends
    a JSON message and expects a JSON response back over the same connection.
    """
    await ws.accept()

    store = getattr(ws.app.state, "store", None)

    # Latest transcript seen from Retell, plus a count of how many turns
    # we've already logged live so we don't repeat them on disconnect.
    latest_transcript: List[Dict[str, str]] = []
    logged_turn_count = 0

    def _log_turn(entry: Dict[str, str]) -> None:
        role = entry.get("role", "?")
        content = (entry.get("content") or "").strip().replace("\n", " ")
        if content:
            logger.info("Retell turn: call_id=%s [%s] %s", call_id, role, content)

    # Send greeting immediately on connection — no LLM call needed
    try:
        await ws.send_text(json.dumps({
            "response_id": 0,
            "content": "Hi, thanks for calling Alex's Plumbing Service! How can I help you today?",
            "content_complete": True,
            "end_call": False,
        }))
    except Exception as exc:
        logger.error("Failed to send greeting: call_id=%s %s", call_id, exc)

    try:
        while True:
            raw = await ws.receive_text()
            body = json.loads(raw)

            interaction_type: str = body.get("interaction_type", "")
            response_id: int = body.get("response_id", 0)
            transcript: List[Dict[str, str]] = body.get("transcript", [])
            call_info: Dict[str, Any] = body.get("call", {})
            call_id = call_info.get("call_id", call_id)

            # Keep the most recent transcript and log any newly-finalized turns.
            if transcript:
                latest_transcript = transcript
                if len(latest_transcript) > logged_turn_count + 1:
                    for entry in latest_transcript[logged_turn_count : len(latest_transcript) - 1]:
                        _log_turn(entry)
                    logged_turn_count = len(latest_transcript) - 1

            # For update_only just acknowledge — no response needed
            if interaction_type == "update_only":
                continue

            # Ensure session exists
            if call_id not in _retell_sessions:
                _retell_sessions[call_id] = {
                    "history": [],
                    "turns": 0,
                    "business_id": settings.DEMO_BUSINESS_ID,
                }

            session = _retell_sessions[call_id]

            # Rebuild history from Retell's authoritative transcript so the
            # assistant always sees the latest user utterance. We only replay
            # plain text turns — tool_use/tool_result blocks from previous
            # iterations live in session["history"] only within a single turn.
            history: List[Dict[str, Any]] = []
            for t in transcript:
                role = "user" if t.get("role") == "user" else "assistant"
                content = (t.get("content") or "").strip()
                if content:
                    history.append({"role": role, "content": content})

            # Ensure the last message is from the user — otherwise there's
            # nothing for the LLM to respond to this turn.
            if not history or history[-1]["role"] != "user":
                await ws.send_text(json.dumps({
                    "response_id": response_id,
                    "content": "Sorry, I didn't catch that — could you say it again?",
                    "content_complete": True,
                    "end_call": False,
                }))
                continue

            # Build a system prompt that includes today's date so the LLM can
            # resolve relative dates like "tomorrow" into concrete ISO strings.
            today_local = datetime.now(SALON_TZ)
            system_with_date = (
                RETELL_SYSTEM_PROMPT
                + f"\n\nTODAY'S DATE: {today_local.strftime('%A, %B %-d, %Y')} "
                f"({today_local.strftime('%Y-%m-%d')}), local time."
            )

            # -------- Tool loop --------
            agent_reply = ""
            try:
                for _ in range(MAX_TOOL_ITERATIONS):
                    resp = await litellm_chat(
                        model=settings.LITELLM_MODEL,
                        max_tokens=300,
                        system=system_with_date,
                        messages=history,
                        tools=RETELL_VOICE_TOOLS,
                    )
                    history.append({"role": "assistant", "content": resp.content})

                    # Collect any plain text the LLM produced this step.
                    text_parts = [
                        getattr(b, "text", "")
                        for b in resp.content
                        if getattr(b, "type", None) == "text"
                    ]
                    step_text = " ".join(p for p in text_parts if p).strip()
                    if step_text:
                        agent_reply = step_text

                    if resp.stop_reason != "tool_use":
                        break

                    # Execute each tool_use block and feed the results back.
                    tool_results = []
                    for block in resp.content:
                        if getattr(block, "type", None) != "tool_use":
                            continue
                        logger.info(
                            "Retell tool_use: call_id=%s name=%s input=%s",
                            call_id, block.name, json.dumps(block.input)[:300],
                        )
                        result_str = await _execute_voice_tool(
                            name=block.name,
                            args=block.input,
                            call_id=call_id,
                            session=session,
                            store=store,
                        )
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_str,
                        })
                    history.append({"role": "user", "content": tool_results})
                else:
                    # Hit the iteration cap without a final text reply.
                    if not agent_reply:
                        agent_reply = (
                            "Let me double-check that and get back to you in a moment."
                        )
            except Exception as exc:
                logger.exception("Retell tool loop failed: call_id=%s %s", call_id, exc)
                agent_reply = "I'm sorry, I ran into a technical issue. Could you say that again?"

            if not agent_reply:
                agent_reply = "Sorry, could you repeat that?"

            session["turns"] += 1
            end_call = False

            # Try to send the reply. If Retell already closed the socket, exit cleanly.
            try:
                await ws.send_text(json.dumps({
                    "response_id": response_id,
                    "content": agent_reply,
                    "content_complete": True,
                    "end_call": end_call,
                }))
            except (WebSocketDisconnect, RuntimeError):
                return

            if end_call:
                _retell_sessions.pop(call_id, None)
                return

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("Retell WebSocket error: call_id=%s %s", call_id, exc)
    finally:
        # Flush any turns that were not yet logged live (typically the
        # final in-progress turn at the moment the call ended).
        for entry in latest_transcript[logged_turn_count:]:
            _log_turn(entry)

        _retell_sessions.pop(call_id, None)
        try:
            await ws.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Tool execution
# --------------------------------------------------------------------------- #

async def _execute_voice_tool(
    name: str,
    args: Dict[str, Any],
    call_id: str,
    session: Dict[str, Any],
    store: Any,
) -> str:
    """
    Dispatch a single tool call from the voice agent.

    Returns a JSON-string result for the `tool_result` block fed back to the LLM.
    On failure, returns a structured error so the model can apologize rather
    than silently confirming a fake booking.
    """
    tool = CalendarTool()

    if name == "check_availability":
        try:
            slots = await tool.get_availability(
                business_id=session.get("business_id", settings.DEMO_BUSINESS_ID),
                duration_minutes=int(args.get("duration_minutes", 60)),
                days_ahead=int(args.get("days_ahead", 7)),
            )
            # Trim to a handful so the LLM reads only a couple back to the caller.
            return json.dumps({"slots": slots[:6]})
        except Exception as exc:
            logger.exception("check_availability failed: call_id=%s %s", call_id, exc)
            return json.dumps({"error": "Could not fetch calendar availability."})

    if name == "book_appointment":
        try:
            appointment_iso = args["appointment_datetime"]
            duration = int(args.get("duration_minutes", 60))
            customer_name = args["customer_name"]
            customer_phone = args["customer_phone"]
            service = args["service"]
            notes = args.get("notes", "") or "Booked via Retell voice call"

            event_id = await tool.book_appointment(
                business_id=session.get("business_id", settings.DEMO_BUSINESS_ID),
                customer_phone=customer_phone,
                customer_name=customer_name,
                service=service,
                appointment_dt=appointment_iso,
                duration_minutes=duration,
                notes=notes,
            )
        except Exception as exc:
            logger.exception("book_appointment failed: call_id=%s %s", call_id, exc)
            return json.dumps({
                "error": "Calendar booking failed. Apologize and offer to try again.",
            })

        # Persist to the store so the dashboard KPI increments and the
        # conversation is visible in the history view.
        try:
            await _persist_voice_booking(
                store=store,
                call_id=call_id,
                session=session,
                customer_name=customer_name,
                customer_phone=customer_phone,
                service=service,
                appointment_iso=appointment_iso,
                event_id=event_id,
            )
        except Exception as exc:
            # Don't fail the tool call just because the store write stumbled —
            # the Google event is already real.
            logger.warning("Could not persist voice booking to store: %s", exc)

        return json.dumps({
            "success": True,
            "calendar_event_id": event_id,
            "appointment_datetime": appointment_iso,
        })

    return json.dumps({"error": f"Unknown tool: {name}"})


async def _persist_voice_booking(
    store: Any,
    call_id: str,
    session: Dict[str, Any],
    customer_name: str,
    customer_phone: str,
    service: str,
    appointment_iso: str,
    event_id: str,
) -> None:
    """
    Create a Conversation with outcome=appointment_booked and update the
    matching customer record. Mirrors seed_appointment.py so the dashboard
    'Appointments Booked' KPI counts voice bookings the same way.
    """
    if store is None:
        logger.warning("No store on app.state — skipping voice-booking persist.")
        return

    # Parse the appointment for a nicely-formatted confirmation string.
    start_dt = datetime.fromisoformat(appointment_iso)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=SALON_TZ)
    pretty_when = start_dt.strftime("%A %b %-d at %-I:%M %p")

    conv = Conversation(
        business_id=session.get("business_id", settings.DEMO_BUSINESS_ID),
        customer_phone=customer_phone,
        agent="booking_boss",
        summary=f"Voice booking: {service} for {customer_name}",
        last_message=f"You're booked for {service} on {pretty_when}.",
        outcome="appointment_booked",
        trigger_event_id=f"retell:{call_id}",
        messages=[
            ConversationMessage(
                role=MessageRole.USER,
                content=f"(Voice call) Customer asked to book {service}.",
            ),
            ConversationMessage(
                role=MessageRole.ASSISTANT,
                content=f"Booked {service} for {customer_name} on {pretty_when}. "
                f"Calendar event: {event_id}.",
            ),
        ],
    )
    await store.save_conversation(conv)
    logger.info(
        "Persisted voice booking conversation %s (call_id=%s, event=%s)",
        conv.id, call_id, event_id,
    )

    # Update the customer record if we can find one (best-effort — keeps the
    # in-memory store consistent but not required for the KPI).
    try:
        customer = await store.get_customer(customer_phone)
        if customer:
            customer.lead_stage = "appointment_booked"
            customer.upcoming_appointment = (
                start_dt if start_dt.tzinfo is None else start_dt.replace(tzinfo=None)
            )
            customer.last_contact_at = datetime.utcnow()
            if customer_name and not customer.name:
                customer.name = customer_name
            await store.save_customer(customer)
    except Exception as exc:
        logger.debug("Customer upsert skipped: %s", exc)
