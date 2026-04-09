"""
LiteLLM adapter that provides an Anthropic-compatible interface.

Converts Anthropic-format requests (tools with input_schema, content blocks)
to OpenAI-compatible format, calls the T-Mobile LiteLLM proxy, and returns
a response object that mimics the Anthropic SDK response.

This lets agent code stay mostly unchanged — just replace:
    await self.client.messages.create(...)
with:
    await litellm_chat(...)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from backend.src.config import settings

logger = logging.getLogger(__name__)

# Shared persistent client — reuses TCP connection across all agent calls,
# eliminating per-request TLS handshake overhead (~200-400ms savings).
_http_client: Optional[httpx.AsyncClient] = None


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=30),
        )
    return _http_client


# ---------------------------------------------------------------------------
# Response objects that mimic anthropic SDK types
# ---------------------------------------------------------------------------

@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: Dict[str, Any]
    type: str = "tool_use"


@dataclass
class LiteLLMResponse:
    stop_reason: str          # "end_turn" or "tool_use"
    content: List[Any]        # list of TextBlock / ToolUseBlock


# ---------------------------------------------------------------------------
# Format converters
# ---------------------------------------------------------------------------

def _anthropic_tools_to_openai(tools: List[Dict]) -> List[Dict]:
    """Convert Anthropic tool definitions to OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


def _anthropic_messages_to_openai(
    system: str, messages: List[Dict]
) -> List[Dict]:
    """
    Convert an Anthropic-format message list (with content block objects or
    tool_result dicts) to an OpenAI-compatible message list.
    """
    oai: List[Dict] = [{"role": "system", "content": system}]

    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if role == "assistant":
            if isinstance(content, list):
                # Content is a list of TextBlock / ToolUseBlock objects (or dicts)
                text_parts: List[str] = []
                tool_calls: List[Dict] = []

                for block in content:
                    # Handle both dataclass objects and plain dicts
                    block_type = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)

                    if block_type == "text":
                        txt = getattr(block, "text", None) or (block.get("text", "") if isinstance(block, dict) else "")
                        if txt:
                            text_parts.append(txt)
                    elif block_type == "tool_use":
                        bid = getattr(block, "id", None) or (block.get("id", "") if isinstance(block, dict) else "")
                        bname = getattr(block, "name", None) or (block.get("name", "") if isinstance(block, dict) else "")
                        binput = getattr(block, "input", None) or (block.get("input", {}) if isinstance(block, dict) else {})
                        tool_calls.append({
                            "id": bid,
                            "type": "function",
                            "function": {
                                "name": bname,
                                "arguments": json.dumps(binput),
                            },
                        })

                assistant_msg: Dict[str, Any] = {
                    "role": "assistant",
                    "content": " ".join(text_parts) if text_parts else None,
                }
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                oai.append(assistant_msg)
            else:
                oai.append({"role": "assistant", "content": content})

        elif role == "user":
            if isinstance(content, list):
                # May be a list of tool_result dicts OR plain string items
                has_tool_results = any(
                    (isinstance(item, dict) and item.get("type") == "tool_result")
                    for item in content
                )
                if has_tool_results:
                    # Each tool_result becomes a separate "tool" role message
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "tool_result":
                            oai.append({
                                "role": "tool",
                                "tool_call_id": item["tool_use_id"],
                                "content": item["content"],
                            })
                else:
                    # Regular user content list — join as text
                    combined = " ".join(str(i) for i in content)
                    oai.append({"role": "user", "content": combined})
            else:
                oai.append({"role": "user", "content": content})

        else:
            oai.append(msg)

    return oai


# ---------------------------------------------------------------------------
# Main async call function
# ---------------------------------------------------------------------------

async def litellm_chat(
    model: str,
    max_tokens: int,
    system: str,
    messages: List[Dict],
    tools: Optional[List[Dict]] = None,
) -> LiteLLMResponse:
    """
    Calls the LiteLLM proxy in OpenAI-compatible format.
    Returns an Anthropic-style LiteLLMResponse for drop-in compatibility.
    """
    oai_messages = _anthropic_messages_to_openai(system, messages)
    oai_tools = _anthropic_tools_to_openai(tools) if tools else None

    payload: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": oai_messages,
    }
    if oai_tools:
        payload["tools"] = oai_tools

    logger.debug("LiteLLM request: model=%s messages=%d tools=%s",
                 model, len(oai_messages), len(oai_tools) if oai_tools else 0)

    client = get_http_client()
    resp = await client.post(
        f"{settings.LITELLM_BASE_URL}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {settings.LITELLM_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
    )

    if resp.status_code != 200:
        logger.error("LiteLLM error %d: %s", resp.status_code, resp.text[:500])
        resp.raise_for_status()

    data = resp.json()

    choice = data["choices"][0]
    finish_reason = choice.get("finish_reason", "stop")
    message = choice["message"]

    content_blocks: List[Any] = []

    if message.get("content"):
        content_blocks.append(TextBlock(text=message["content"]))

    if message.get("tool_calls"):
        for tc in message["tool_calls"]:
            try:
                args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, KeyError):
                args = {}
            content_blocks.append(ToolUseBlock(
                id=tc["id"],
                name=tc["function"]["name"],
                input=args,
            ))

    # Map OpenAI finish_reason → Anthropic stop_reason
    stop_reason = "tool_use" if finish_reason == "tool_calls" else "end_turn"

    logger.debug("LiteLLM response: stop_reason=%s content_blocks=%d", stop_reason, len(content_blocks))
    return LiteLLMResponse(stop_reason=stop_reason, content=content_blocks)


async def litellm_classify(
    system: str,
    user_message: str,
    max_tokens: int = 150,
) -> str:
    """
    Simple non-tool LiteLLM call for classification tasks.
    Returns the raw text response.
    """
    client = get_http_client()
    resp = await client.post(
        f"{settings.LITELLM_BASE_URL}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {settings.LITELLM_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": settings.LITELLM_MODEL,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
        },
    )
    resp.raise_for_status()
    data = resp.json()

    return data["choices"][0]["message"]["content"].strip()
