# T-CHai / SMB-in-a-Box — Session Handover

**Date:** 2026-04-09
**Repo:** `/Users/deequez/T-CHai/agentx`
**Team:** T-Mobile Hackathon 2026

---

## Project in one sentence
FastAPI + React app that turns T-Mobile into an "AI carrier" for SMBs — 5 Claude-powered agents (LeadCatcher, ReviewPilot, AfterHours, BookingBoss, Campaign) handle customer comms via SMS, plus a voice feature via **OpenAI Realtime** *and* **Retell AI**.

---

## What was accomplished in this session

### 1. Got the project running locally (no Docker)
- User had **no Python installed** at start. Chose Python path over Docker after initial Docker attempt hit an f-string syntax error on Python 3.11.
- Fixed `backend/src/api/routes/voice.py:83,86` — backslash-escaped quotes in f-strings (invalid on 3.11, legal on 3.12+). Pulled the strings into variables before the f-string.
- Backend runs via: `source .venv/bin/activate && uvicorn backend.server:app --port 8000`
- Frontend: the `frontend/tablet` React app was partially built. Upgraded Node from 20.10 → **Node 22 via Homebrew** (Vite requires 20.19+).
- Fixed `frontend/tablet/vite.config.js` — proxy was pointing at port **8100**, corrected to **8000**. Also added `allowedHosts: ["financial-sandstone-flakily.ngrok-free.dev"]` to `preview` config.

### 2. Built a full Retell AI integration alongside the existing OpenAI Realtime path
The existing OpenAI Realtime voice (`VoiceCall.jsx` + `/api/v1/voice/ws`) is **untouched and still works**. Retell was added as a parallel provider with a UI toggle so both can be demoed.

**New files:**
- `backend/src/api/routes/retell_voice.py` — Two endpoints:
  - `POST /api/v1/voice/retell/register-call` — calls Retell REST API, returns an `access_token` to the browser
  - `WS /api/v1/voice/retell/llm-webhook/{call_id}` — Retell connects via **WebSocket** (not HTTP!) on each call and sends conversation turns; we reply in Retell's custom-LLM JSON format
- `frontend/tablet/src/VoiceCallRetell.jsx` — React component using `retell-client-js-sdk`, same visual style as `VoiceCall.jsx` with a "Retell AI" badge

**Modified files:**
- `backend/src/config/settings.py` — added `RETELL_API_KEY`, `RETELL_AGENT_ID`, `VOICE_PROVIDER`
- `backend/server.py` — registered `retell_voice.router` at `/api/v1/voice/retell`
- `frontend/tablet/src/App.jsx` — added `voiceProvider` state and a **pill toggle** (OpenAI / Retell) in the header near "Simulate Call"
- `frontend/tablet/src/App.css` — added `.voice-toggle` and `.voice-modal` styles
- `.env.example` — added Retell key placeholders
- `frontend/tablet/package.json` — installed `retell-client-js-sdk`

### 3. Latency optimization (critical — original was 18s per turn)
The first Retell implementation called the full `Orchestrator.handle()` which does:
1. Claude classification call (intent routing)
2. Specialist agent call (full agentic loop with tools)

That's 2-4 sequential Claude calls per turn = ~18 seconds. Unusable for voice.

**Fix:** In `retell_voice.py`, the Retell webhook now skips the orchestrator entirely and makes **one direct LLM call** via `litellm_classify()` with a voice-tuned `RETELL_SYSTEM_PROMPT` (defined at the top of `retell_voice.py`). Dropped latency from **18s → ~3s** (confirmed by Retell's End-to-End Latency metric: 3148ms).

The SMS-facing agents still use the full orchestrator — only Retell voice uses the fast path.

---

## Current working state

### What's working
- Backend runs on port 8000 (Python 3.12 venv at `.venv/`)
- Frontend runs on port 3001 via `npm run preview -- --port 3001` (after `npm run build`)
- ngrok tunnels on port 3001 (frontend) and port 8000 (backend)
  - Frontend ngrok: `https://financial-sandstone-flakily.ngrok-free.dev`
  - Backend ngrok URL changes per session — user sets it manually
- **OpenAI Realtime voice** — works end-to-end via `VoiceCall.jsx` → `/api/v1/voice/ws`
- **Retell AI voice** — works end-to-end via `VoiceCallRetell.jsx` → Retell infra → `/api/v1/voice/retell/llm-webhook/{call_id}` → Claude via LiteLLM
- **Real inbound phone calls** confirmed working: T-Mobile line → `*72<retell-number>` call forwarding → Retell number → Retell → our Claude webhook
- Dashboard, SMS simulate endpoint, conversation history — all working

### Known issues / rough edges
1. **No initial greeting from the Retell agent.** Call transcript shows user speaking first. The Custom LLM agent in the Retell dashboard doesn't expose a "Begin Message" field. **Fix pending:** user needs to PATCH the agent via Retell REST API:
   ```bash
   curl -X PATCH "https://api.retellai.com/update-agent/agent_2d464efbb24cfa3a57acc715c7" \
     -H "Authorization: Bearer $RETELL_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"begin_message": "Hi! Thanks for calling Andy Plumbing, how can I help?"}'
   ```
2. **No UI chat interface at all for the tablet.** The main dashboard works but the voice modal is all we have on the voice side. The `frontend/mobile` directory is a README stub — not implemented.
3. **Long backend logs spam** from Retell connection closing mid-response — *already fixed*, webhook now catches `WebSocketDisconnect` and `RuntimeError` when sending into a closed socket, logs cleanly and exits.
4. **No OpenAI API key** for the Realtime voice path — the user had one set in `.env` (`LITELLM_API_KEY=sk-proj-...`) pointing at `LITELLM_BASE_URL=https://api.openai.com`. This works for the WebSocket relay in `voice.py`.
5. **T-Mobile corporate network blocks ngrok** — the demo laptop can only reach the public URL via a non-corporate network (mobile hotspot) or from outside the corporate VPN.

---

## Key files to know

| File | Purpose |
|------|---------|
| `backend/server.py` | FastAPI entry point, router registration |
| `backend/src/config/settings.py` | All env vars (Pydantic BaseSettings) |
| `backend/src/api/routes/voice.py` | **Existing** OpenAI Realtime WebSocket relay + Twilio voice IVR |
| `backend/src/api/routes/retell_voice.py` | **New** Retell integration (register-call + llm-webhook WS) |
| `backend/src/agents/orchestrator.py` | SMS orchestrator — NOT used by Retell (too slow for voice) |
| `backend/src/agents/litellm_client.py` | `litellm_classify()` helper — used directly by retell_voice.py for fast replies |
| `backend/src/db/store.py` | In-memory store with Pete's Plumbing pre-loaded |
| `frontend/tablet/src/App.jsx` | Main React app, dashboard, voice provider toggle |
| `frontend/tablet/src/VoiceCall.jsx` | OpenAI Realtime voice UI (unchanged) |
| `frontend/tablet/src/VoiceCallRetell.jsx` | Retell voice UI (new) |
| `frontend/tablet/vite.config.js` | Dev server proxy to backend on port 8000 |

---

## Environment variables (.env)

The user's actual `.env` has real API keys — **do not commit**. Currently set:
- `LITELLM_BASE_URL=https://api.openai.com` (pointing at OpenAI directly, not T-Mobile's internal LiteLLM proxy)
- `LITELLM_API_KEY=sk-proj-...` (real OpenAI key)
- `ANTHROPIC_API_KEY=sk-ant-api03-...` (real Anthropic key)
- `ANTHROPIC_MODEL=claude-sonnet-4-6`
- `RETELL_API_KEY=...`
- `RETELL_AGENT_ID=agent_2d464efbb24cfa3a57acc715c7`
- `VOICE_PROVIDER=openai_realtime` (default; frontend toggle overrides per-session)
- `USE_IN_MEMORY_STORE=true`
- `DEMO_BUSINESS_ID=demo-petes-plumbing`

---

## How to run everything (from scratch)

```bash
# Terminal 1 — Backend
cd /Users/deequez/T-CHai/agentx
source .venv/bin/activate
uvicorn backend.server:app --port 8000

# Terminal 2 — Frontend (Node 22 required)
export PATH="/opt/homebrew/opt/node@22/bin:$PATH"
cd /Users/deequez/T-CHai/agentx/frontend/tablet
npm run build && npm run preview -- --port 3001

# Terminal 3 — Frontend tunnel
/opt/homebrew/bin/ngrok http 3001

# Terminal 4 — Backend tunnel (for Retell webhook)
/opt/homebrew/bin/ngrok http 8000
# → Copy the https URL and update the Retell Custom LLM URL in dashboard.retellai.com
#   to wss://<new-url>/api/v1/voice/retell/llm-webhook
```

### Key URLs
- Frontend (local): http://localhost:3001
- Backend (local): http://localhost:8000
- Backend API docs: http://localhost:8000/docs
- Frontend (public): https://financial-sandstone-flakily.ngrok-free.dev
- Retell dashboard: https://dashboard.retellai.com/agents/agent_2d464efbb24cfa3a57acc715c7

---

## Retell setup (already done, for reference)

- **Custom LLM** created, WebSocket URL points to `wss://<backend-ngrok>/api/v1/voice/retell/llm-webhook` (Retell appends `/{call_id}` automatically)
- **Agent** wired to the Custom LLM, using voice "Cimo"
- **Phone number** purchased from Retell, assigned to the agent as Inbound Agent
- **Call forwarding** from user's T-Mobile line to Retell number via `*72<number>` (unconditional forward-all)

---

## Immediate next steps for a new session

1. **Set the Retell begin message** via the curl command above so the agent greets callers first
2. Consider streaming responses from `litellm_classify` to Retell (send partial `content_complete: false` chunks) to drop latency further from 3s → sub-1s
3. The frontend has no live conversation view for Retell calls on the dashboard — Retell transcripts only show in Retell's dashboard, not ours
4. There's no persistence of Retell conversations to the `store` — they're ephemeral per-call in `_retell_sessions` dict

---

## Things NOT to break
- The OpenAI Realtime path (`VoiceCall.jsx`, `voice.py` WebSocket relay) — this is the "polished" demo path
- The existing Orchestrator + 5 SMS agents — these are the core "agent bundle" story
- The in-memory store pre-seeded with Pete's Plumbing (`backend/src/db/store.py`) — demo relies on it

---

## Session file with all assistant responses
`/Users/deequez/T-CHai/agentx/session.log` — contains a running log of every response from the first session. Has been manually updated a few times by user/linter.
