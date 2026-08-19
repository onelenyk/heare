"""Chat completions over raw HTTP, from whichever provider is selected.

Constraints: stdlib + httpx only. No openai sdk. The provider registry
(``src.agent.llm.providers``) is stdlib-only itself, so it can be
imported here — the copied DeepSeek constants this file used to keep
were a workaround for a pipecat dependency that no longer exists.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger("heare.spine.llm")


@dataclass(frozen=True)
class LLMConfig:
    """Everything needed to open a chat-completions stream. Frozen: a
    resolved config must not be mutated after the fact."""

    base_url: str
    api_key: str
    model: str
    provider: str = "deepseek"
    api_style: str = "openai"


def resolve_llm(settings: object, provider: str | None = None) -> LLMConfig:
    """The provider you chose — or the nearest one that can actually answer.

    The dashboard has offered a provider and model switch since long
    before this engine existed, and until now it changed nothing: the base
    URL and the model were two constants at the top of this file. Choosing
    was theatre, and the only way to find that out was to notice the reply
    still sounded like DeepSeek.

    A chosen provider with no key falls back to one that has, loudly. The
    alternative is a voice assistant that goes mute because of a dropdown,
    and the dashboard already shows which providers are configured.
    """
    from src.agent.llm.providers import PROVIDERS

    wanted = (provider or getattr(settings, "llm_provider", "") or "deepseek").lower()
    for name in [wanted] + [k for k in PROVIDERS if k != wanted]:
        cfg = PROVIDERS.get(name)
        if cfg is None:
            continue
        api_key = getattr(settings, cfg.api_key_attr, None)
        if not api_key:
            continue
        if name != wanted:
            logger.warning(
                "resolve_llm: %s has no key — falling back to %s", wanted, name
            )
        return LLMConfig(
            base_url=getattr(settings, f"{name}_base_url", "") or cfg.base_url,
            api_key=api_key,
            model=getattr(settings, f"{name}_model", "") or cfg.default_model,
            provider=name,
            api_style=cfg.api_style,
        )
    raise RuntimeError(
        "resolve_llm: no provider has an API key — set one of "
        + ", ".join(c.api_key_env for c in PROVIDERS.values())
    )


async def stream_chat_events(
    messages: list[dict],
    cfg: LLMConfig,
    *,
    tools: list[dict] | None = None,
    client: httpx.AsyncClient | None = None,
    temperature: float = 0.7,
    timeout: float = 60.0,
) -> AsyncIterator[dict]:
    """One stream of events, whichever protocol the provider speaks.

    Both branches yield the same three shapes — ``delta``, ``tool_call``,
    ``usage`` — so nothing downstream knows or cares who answered. That is
    the whole point of putting the fork here: the conductor, the sentence
    splitter and the usage ledger were written against OpenAI's wire format
    by accident of which provider came first, not by choice.
    """
    if cfg.api_style == "anthropic":
        stream = _anthropic_events(
            messages, cfg, tools=tools, client=client,
            temperature=temperature, timeout=timeout,
        )
    else:
        stream = _openai_events(
            messages, cfg, tools=tools, client=client,
            temperature=temperature, timeout=timeout,
        )
    async for event in stream:
        yield event


async def _openai_events(
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
        # The final SSE chunk then carries prompt/completion token
        # counts — without it every LLM call is invisible to the
        # usage_events accounting (the daemon's worker had exactly
        # this hole).
        "stream_options": {"include_usage": True},
    }
    if tools is not None:
        payload["tools"] = tools

    owns_client = client is None
    http_client = client if client is not None else httpx.AsyncClient(timeout=timeout)
    tool_calls: dict[int, dict[str, Any]] = {}
    usage: dict[str, Any] | None = None
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
                if chunk.get("usage"):
                    usage = {
                        "type": "usage",
                        "model": chunk.get("model") or cfg.model,
                        "input_tokens": chunk["usage"].get("prompt_tokens", 0),
                        "output_tokens": chunk["usage"].get(
                            "completion_tokens", 0
                        ),
                    }
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
        if usage is not None:
            yield usage
    finally:
        if owns_client:
            await http_client.aclose()


def _to_anthropic(
    messages: list[dict], tools: list[dict] | None
) -> tuple[str, list[dict], list[dict] | None]:
    """Translate an OpenAI-shaped conversation into Anthropic's.

    Three differences, and only three, because of what this engine does
    not do: it never feeds a tool result back to the model. Tools are run
    after the reply and their acknowledgement is spoken, so there are no
    ``tool_result`` blocks to carry across — only plain turns.

    * the system prompt is a top-level field, not a message;
    * consecutive same-role turns are illegal, so they are joined;
    * a tool's JSON Schema is ``input_schema``, not ``function.parameters``.
    """
    system_parts: list[str] = []
    turns: list[dict] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content") or ""
        if role == "system":
            system_parts.append(str(content))
            continue
        if role not in ("user", "assistant") or not content:
            continue
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n\n" + str(content)
        else:
            turns.append({"role": role, "content": str(content)})

    declared = None
    if tools is not None:
        declared = []
        for tool in tools:
            function = tool.get("function") or tool
            declared.append(
                {
                    "name": function.get("name", ""),
                    "description": function.get("description", ""),
                    "input_schema": function.get("parameters")
                    or {"type": "object", "properties": {}},
                }
            )
    return "\n\n".join(system_parts), turns, declared


async def _anthropic_events(
    messages: list[dict],
    cfg: LLMConfig,
    *,
    tools: list[dict] | None = None,
    client: httpx.AsyncClient | None = None,
    temperature: float = 0.7,
    timeout: float = 60.0,
    max_tokens: int = 1024,
) -> AsyncIterator[dict]:
    """POST {base_url}/v1/messages with stream=true, yielding the same
    events as the OpenAI branch.

    Written against Anthropic's published event stream and covered by
    fixtures rather than by a live call: the one provider here that speaks
    it, Z.AI, has no key on this machine. The shapes are exercised; the
    handshake is not.

    ``max_tokens`` is required by the protocol and has no default there.
    1024 is generous for something whose replies are spoken aloud.
    """
    system, turns, declared = _to_anthropic(messages, tools)
    url = f"{cfg.base_url.rstrip('/')}/v1/messages"
    headers = {
        "x-api-key": cfg.api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": cfg.model,
        "messages": turns,
        "stream": True,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if system:
        payload["system"] = system
    if declared is not None:
        payload["tools"] = declared

    owns_client = client is None
    http_client = client if client is not None else httpx.AsyncClient(timeout=timeout)
    blocks: dict[int, dict[str, Any]] = {}
    input_tokens = 0
    output_tokens = 0
    model = cfg.model
    try:
        async with http_client.stream(
            "POST", url, json=payload, headers=headers
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    chunk = json.loads(line[len("data: ") :].strip())
                except json.JSONDecodeError:
                    continue
                kind = chunk.get("type")
                if kind == "message_start":
                    message = chunk.get("message") or {}
                    model = message.get("model") or model
                    input_tokens = (message.get("usage") or {}).get(
                        "input_tokens", 0
                    )
                elif kind == "content_block_start":
                    block = chunk.get("content_block") or {}
                    if block.get("type") == "tool_use":
                        blocks[chunk.get("index", 0)] = {
                            "id": block.get("id"),
                            "name": block.get("name"),
                            "arguments": "",
                        }
                elif kind == "content_block_delta":
                    delta = chunk.get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        yield {"type": "delta", "text": delta["text"]}
                    elif delta.get("type") == "input_json_delta":
                        entry = blocks.get(chunk.get("index", 0))
                        if entry is not None:
                            entry["arguments"] += delta.get("partial_json", "")
                elif kind == "message_delta":
                    output_tokens = (chunk.get("usage") or {}).get(
                        "output_tokens", output_tokens
                    )
        for index in sorted(blocks):
            entry = blocks[index]
            # An empty tool_use carries no arguments at all; the caller
            # parses this string, and "" is not JSON.
            yield {
                "type": "tool_call",
                "id": entry["id"],
                "name": entry["name"],
                "arguments": entry["arguments"] or "{}",
            }
        if input_tokens or output_tokens:
            yield {
                "type": "usage",
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
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
