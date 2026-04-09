#!/usr/bin/env python3
"""
AgentX Voice Simulation
=======================
Simulates a real inbound call locally — no phone, no Twilio, no public URL needed.

Flow:
  speak → Whisper STT → AgentX backend → Claude agent → macOS TTS → speak back

Usage:
  python3 tools/voice_sim.py

Requirements:
  pip install openai-whisper sounddevice numpy
  Backend must be running: uvicorn backend.server:app --port 8100
"""

import argparse
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path

import numpy as np
import sounddevice as sd
import whisper

# ── Config ──────────────────────────────────────────────────────────────────
BACKEND_URL   = "http://localhost:8100"
BUSINESS_ID   = "demo-petes-plumbing"
TTS_VOICE     = "Samantha"   # macOS voice — change to "Daniel" for male
SAMPLE_RATE   = 16000        # Hz — Whisper native rate
SILENCE_THRESHOLD = 0.01     # RMS below this = silence
SILENCE_SECS  = 1.8          # seconds of silence before we stop recording
MAX_RECORD_SECS = 30         # safety cap per turn
WHISPER_MODEL = "turbo"      # turbo = best speed/quality balance on Apple Silicon

# ── Colours ─────────────────────────────────────────────────────────────────
CYAN  = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED   = "\033[91m"
RESET = "\033[0m"
BOLD  = "\033[1m"


def log(color: str, prefix: str, msg: str) -> None:
    print(f"{color}{BOLD}{prefix}{RESET} {msg}")


def check_backend() -> bool:
    try:
        with urllib.request.urlopen(f"{BACKEND_URL}/healthz", timeout=3) as r:
            data = json.loads(r.read())
            return data.get("status") == "ok"
    except Exception:
        return False


def speak(text: str) -> None:
    """Speak text using macOS TTS — blocks until done."""
    # Strip markdown and emojis for clean speech
    import re
    text = re.sub(r'[^\x00-\x7F]+', '', text)
    text = re.sub(r'\*+', '', text)
    text = re.sub(r'#+\s*', '', text)
    text = re.sub(r'\|[^\n]*', '', text)
    text = re.sub(r'-{3,}', '', text)
    text = re.sub(r'\n+', ' ', text)
    text = re.sub(r'\s{2,}', ' ', text).strip()
    if not text:
        return
    subprocess.run(["say", "-v", TTS_VOICE, text], check=False)


def record_until_silence() -> np.ndarray:
    """
    Record from mic until SILENCE_SECS of silence detected.
    Returns numpy float32 array at SAMPLE_RATE.
    """
    log(CYAN, "🎤", "Listening... (speak now, pause when done)")

    frames = []
    silent_chunks = 0
    chunk_duration = 0.1   # seconds per chunk
    chunk_size = int(SAMPLE_RATE * chunk_duration)
    max_chunks = int(MAX_RECORD_SECS / chunk_duration)
    silence_chunks_needed = int(SILENCE_SECS / chunk_duration)
    started_speaking = False

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                        dtype="float32", blocksize=chunk_size) as stream:
        for _ in range(max_chunks):
            chunk, _ = stream.read(chunk_size)
            rms = float(np.sqrt(np.mean(chunk ** 2)))
            frames.append(chunk.copy())

            if rms > SILENCE_THRESHOLD:
                started_speaking = True
                silent_chunks = 0
            elif started_speaking:
                silent_chunks += 1
                if silent_chunks >= silence_chunks_needed:
                    break

    audio = np.concatenate(frames, axis=0).flatten()
    return audio


def transcribe(model: whisper.Whisper, audio: np.ndarray) -> str:
    """Transcribe audio array using Whisper."""
    log(YELLOW, "⚙️ ", "Transcribing...")
    result = model.transcribe(audio, language="en", fp16=False)
    text = result["text"].strip()
    return text


def call_agent(message: str, conversation_id: str | None) -> dict:
    """POST to backend simulate endpoint, return result dict."""
    payload = {
        "message": message,
        "business_id": BUSINESS_ID,
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{BACKEND_URL}/api/v1/events/simulate",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def print_banner() -> None:
    print(f"""
{BOLD}{CYAN}╔══════════════════════════════════════════════╗
║        AgentX Voice Simulation              ║
║  Speak naturally — AI agent will respond    ║
╚══════════════════════════════════════════════╝{RESET}

  • Speak after the 🎤 prompt
  • Pause {SILENCE_SECS}s when done talking
  • Press {BOLD}Ctrl+C{RESET} to end the call
""")


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentX local voice simulation")
    parser.add_argument("--voice", default=TTS_VOICE,
                        help=f"macOS TTS voice (default: {TTS_VOICE})")
    parser.add_argument("--model", default=WHISPER_MODEL,
                        choices=["tiny", "base", "small", "medium", "large", "turbo"],
                        help="Whisper model size (default: turbo)")
    args = parser.parse_args()

    print_banner()

    # Check backend
    log(YELLOW, "⏳", "Checking backend...")
    if not check_backend():
        log(RED, "✗", f"Backend not reachable at {BACKEND_URL}")
        log(RED, " ", "Run: uvicorn backend.server:app --port 8100")
        sys.exit(1)
    log(GREEN, "✓", "Backend is running")

    # Load Whisper model
    log(YELLOW, "⏳", f"Loading Whisper {args.model} model (first run downloads it)...")
    model = whisper.load_model(args.model)
    log(GREEN, "✓", f"Whisper {args.model} ready")

    # Greeting
    greeting = "Hi! Thanks for calling Andy Plumbing. How can I help you today?"
    log(GREEN, "🤖", f"Agent: {greeting}")
    speak(greeting)

    conversation_id = None
    turn = 0

    try:
        while True:
            turn += 1
            print()

            # Record caller speech
            audio = record_until_silence()

            # Transcribe
            caller_said = transcribe(model, audio)
            if not caller_said:
                speak("Sorry, I didn't catch that. Could you repeat?")
                continue

            log(CYAN, "👤", f"You: {caller_said}")

            # Check for goodbye
            if any(w in caller_said.lower() for w in ["goodbye", "bye", "hang up", "that's all"]):
                closing = "Great! We'll see you soon. Have a wonderful day, goodbye!"
                log(GREEN, "🤖", f"Agent: {closing}")
                speak(closing)
                break

            # Call agent
            log(YELLOW, "⚙️ ", "Agent thinking...")
            t0 = time.time()
            try:
                result = call_agent(caller_said, conversation_id)
            except urllib.error.URLError as e:
                log(RED, "✗", f"Backend error: {e}")
                speak("I'm having a technical issue. Please try again.")
                continue

            elapsed = time.time() - t0
            agent_reply = result.get("agent_reply", "")
            agent_used  = result.get("agent_used", "orchestrator")
            conversation_id = result.get("conversation_id", conversation_id)

            log(GREEN, "🤖", f"[{agent_used} • {elapsed:.1f}s] {agent_reply}")
            speak(agent_reply)

    except KeyboardInterrupt:
        print(f"\n{YELLOW}Call ended.{RESET}")
        speak("Call ended. Thank you for calling Andy Plumbing!")


if __name__ == "__main__":
    main()
