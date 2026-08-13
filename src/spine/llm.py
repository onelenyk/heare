"""DeepSeek chat completions over raw HTTP.

Constraints: stdlib + httpx only. No pipecat, no openai sdk, no import
of src.agent.llm.providers (that module may pull in pipecat at import
time via its factory functions) — the DeepSeek defaults below are
copied from ``PROVIDERS["deepseek"]`` in that file, not imported from it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

# Copied from src.agent.llm.providers.PROVIDERS["deepseek"] — do not
# import that module here, it may drag in pipecat.
_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
_DEEPSEEK_MODEL = "deepseek-chat"


@dataclass(frozen=True)
class LLMConfig:
    """Everything needed to open a chat-completions stream. Frozen: a
    resolved config must not be mutated after the fact."""

    base_url: str
    api_key: str
    model: str


def resolve_llm(settings: object) -> LLMConfig:
    """DeepSeek from settings (deepseek_api_key required; deepseek_base_url /
    deepseek_model override the registry defaults copied here).
    Raises RuntimeError with a clear message if no key."""
    api_key = getattr(settings, "deepseek_api_key", None)
    if not api_key:
        raise RuntimeError(
            "resolve_llm: settings.deepseek_api_key is not set — "
            "DeepSeek requires an API key"
        )
    base_url = getattr(settings, "deepseek_base_url", None) or _DEEPSEEK_BASE_URL
    model = getattr(settings, "deepseek_model", None) or _DEEPSEEK_MODEL
    return LLMConfig(base_url=base_url, api_key=api_key, model=model)


async def stream_chat_events(
    messages: list[dict],
    cfg: LLMConfig,
    *,
    tools: list[dict] | None = None,
    client: httpx.AsyncClient | None = None,
    temperature: float = 0.7,
    timeout: float = 60.0,
) -> AsyncIterator[dict]:
    """POST {base_url}/chat/completions with stream=true; yield
    {"type": "delta", "text": str} for each content delta as it
    arrives, then (after the stream ends) one {"type": "tool_call",
    "id": str, "name": str, "arguments": str} per completed tool
    call, in index order. `arguments` is the raw accumulated JSON
    string — the caller parses it.

    OpenAI-compatible SSE: lines 'data: {json}', terminator
    'data: [DONE]'. Empty/None content deltas are skipped.
    choices[0].delta.tool_calls fragments are accumulated by their
    "index": the id and function name typically arrive on the first
    fragment for that index, and function.arguments chunks are
    concatenated across fragments in the order received. Content
    deltas may be interleaved with tool-call fragments.

    When `tools` is None the request omits the "tools" field
    entirely. Caller's client is used and not closed; otherwise one
    is created per call. Non-2xx raises httpx.HTTPStatusError before
    any yield."""
    url = f"{cfg.base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "stream": True,
        "temperature": temperature,
    }
    if tools is not None:
        payload["tools"] = tools

    owns_client = client is None
    http_client = client if client is not None else httpx.AsyncClient(timeout=timeout)
    tool_calls: dict[int, dict[str, Any]] = {}
    try:
        async with http_client.stream(
            "POST", url, json=payload, headers=headers
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[len("data: ") :].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    yield {"type": "delta", "text": content}
                for frag in delta.get("tool_calls") or []:
                    idx = frag.get("index", 0)
                    entry = tool_calls.setdefault(
                        idx, {"id": None, "name": None, "arguments": ""}
                    )
                    if frag.get("id"):
                        entry["id"] = frag["id"]
                    func = frag.get("function") or {}
                    if func.get("name"):
                        entry["name"] = func["name"]
                    if func.get("arguments"):
                        entry["arguments"] += func["arguments"]
        for idx in sorted(tool_calls):
            entry = tool_calls[idx]
            yield {
                "type": "tool_call",
                "id": entry["id"],
                "name": entry["name"],
                "arguments": entry["arguments"],
            }
    finally:
        if owns_client:
            await http_client.aclose()


async def stream_chat(
    messages: list[dict],
    cfg: LLMConfig,
    *,
    client: httpx.AsyncClient | None = None,
    temperature: float = 0.7,
    timeout: float = 60.0,
) -> AsyncIterator[str]:
    """POST {base_url}/chat/completions with stream=true; yield content
    deltas as they arrive. Thin wrapper over stream_chat_events that
    filters to "delta" events and yields their text — behaviour and
    signature unchanged from before tool-call support was added."""
    async for event in stream_chat_events(
        messages,
        cfg,
        tools=None,
        client=client,
        temperature=temperature,
        timeout=timeout,
    ):
        if event["type"] == "delta":
            yield event["text"]
