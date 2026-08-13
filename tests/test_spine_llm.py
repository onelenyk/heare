"""Tests for src/spine/llm.py — no network, httpx.MockTransport only."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from src.spine.llm import LLMConfig, resolve_llm, stream_chat, stream_chat_events


def _sse(*data_lines: str) -> bytes:
    """Build an SSE body from a sequence of 'data: ...' payload strings."""
    return ("".join(f"data: {line}\n\n" for line in data_lines)).encode()


def _delta_line(content: str | None = None, role: str | None = None) -> str:
    delta: dict[str, Any] = {}
    if role is not None:
        delta["role"] = role
    if content is not None:
        delta["content"] = content
    return json.dumps({"choices": [{"delta": delta, "index": 0}]})


CFG = LLMConfig(
    base_url="https://api.deepseek.com/v1",
    api_key="test-key",
    model="deepseek-chat",
)


async def _collect(gen) -> list[str]:
    out = []
    async for chunk in gen:
        out.append(chunk)
    return out


@pytest.mark.asyncio
async def test_deltas_yielded_in_order_and_concatenate():
    body = _sse(
        _delta_line(role="assistant"),  # role-only, no content
        _delta_line(content="Hel"),
        _delta_line(content="lo, "),
        _delta_line(content="world"),
        _delta_line(content="!"),
        "[DONE]",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        chunks = await _collect(
            stream_chat([{"role": "user", "content": "hi"}], CFG, client=client)
        )

    assert chunks == ["Hel", "lo, ", "world", "!"]
    assert "".join(chunks) == "Hello, world!"


@pytest.mark.asyncio
async def test_done_terminates_cleanly():
    body = _sse(
        _delta_line(content="only"),
        "[DONE]",
        _delta_line(content="should-not-appear"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        chunks = await _collect(
            stream_chat([{"role": "user", "content": "hi"}], CFG, client=client)
        )

    assert chunks == ["only"]


@pytest.mark.asyncio
async def test_delta_without_content_is_skipped():
    body = _sse(
        _delta_line(role="assistant"),
        _delta_line(),  # empty delta object, no content and no role
        _delta_line(content="text"),
        "[DONE]",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        chunks = await _collect(
            stream_chat([{"role": "user", "content": "hi"}], CFG, client=client)
        )

    assert chunks == ["text"]


@pytest.mark.asyncio
async def test_non_200_raises_and_yields_nothing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"error": {"message": "invalid api key"}}
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        gen = stream_chat([{"role": "user", "content": "hi"}], CFG, client=client)
        with pytest.raises(httpx.HTTPStatusError):
            await _collect(gen)


@pytest.mark.asyncio
async def test_request_body_contains_model_messages_stream():
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=_sse(_delta_line(content="ok"), "[DONE]"))

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        messages = [{"role": "user", "content": "ping"}]
        await _collect(stream_chat(messages, CFG, client=client))

    assert captured["body"]["model"] == "deepseek-chat"
    assert captured["body"]["messages"] == messages
    assert captured["body"]["stream"] is True


def test_resolve_llm_no_key_raises_runtime_error():
    settings = SimpleNamespace(deepseek_api_key=None)
    with pytest.raises(RuntimeError):
        resolve_llm(settings)


def test_resolve_llm_defaults_match_deepseek_registry():
    settings = SimpleNamespace(deepseek_api_key="k")
    cfg = resolve_llm(settings)
    assert cfg.base_url == "https://api.deepseek.com/v1"
    assert cfg.model == "deepseek-chat"
    assert cfg.api_key == "k"


def test_resolve_llm_settings_overrides_respected():
    settings = SimpleNamespace(
        deepseek_api_key="k2",
        deepseek_base_url="https://custom.example/v1",
        deepseek_model="deepseek-reasoner",
    )
    cfg = resolve_llm(settings)
    assert cfg.base_url == "https://custom.example/v1"
    assert cfg.model == "deepseek-reasoner"
    assert cfg.api_key == "k2"


# --- stream_chat_events (tool calls) -----------------------------------


def _tool_call_line(
    tool_calls_fragments: list[dict[str, Any]] | None = None,
    content: str | None = None,
) -> str:
    delta: dict[str, Any] = {}
    if content is not None:
        delta["content"] = content
    if tool_calls_fragments is not None:
        delta["tool_calls"] = tool_calls_fragments
    return json.dumps({"choices": [{"delta": delta, "index": 0}]})


async def _collect_events(gen) -> list[dict[str, Any]]:
    out = []
    async for event in gen:
        out.append(event)
    return out


@pytest.mark.asyncio
async def test_tools_passed_adds_tools_to_body_none_omits_it():
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, content=_sse(_delta_line(content="ok"), "[DONE]"))

    tools = [
        {
            "type": "function",
            "function": {"name": "do_thing", "parameters": {"type": "object"}},
        }
    ]

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        messages = [{"role": "user", "content": "hi"}]
        await _collect_events(
            stream_chat_events(messages, CFG, tools=tools, client=client)
        )
        await _collect_events(
            stream_chat_events(messages, CFG, tools=None, client=client)
        )

    assert captured[0]["tools"] == tools
    assert "tools" not in captured[1]


@pytest.mark.asyncio
async def test_content_only_stream_yields_only_deltas():
    body = _sse(
        _delta_line(role="assistant"),
        _delta_line(content="Hel"),
        _delta_line(content="lo, "),
        _delta_line(content="world"),
        _delta_line(content="!"),
        "[DONE]",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        events = await _collect_events(
            stream_chat_events([{"role": "user", "content": "hi"}], CFG, client=client)
        )

    assert all(e["type"] == "delta" for e in events)
    assert "".join(e["text"] for e in events) == "Hello, world!"


@pytest.mark.asyncio
async def test_single_tool_call_split_across_fragments_accumulates_arguments():
    body = _sse(
        _tool_call_line(
            [
                {
                    "index": 0,
                    "id": "call_1",
                    "function": {"name": "search", "arguments": ""},
                }
            ]
        ),
        _tool_call_line(
            [{"index": 0, "function": {"arguments": '{"ta'}}]
        ),
        _tool_call_line(
            [{"index": 0, "function": {"arguments": 'sk": "x"}'}}]
        ),
        "[DONE]",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        events = await _collect_events(
            stream_chat_events([{"role": "user", "content": "hi"}], CFG, client=client)
        )

    tool_calls = [e for e in events if e["type"] == "tool_call"]
    assert len(tool_calls) == 1
    assert tool_calls[0] == {
        "type": "tool_call",
        "id": "call_1",
        "name": "search",
        "arguments": '{"task": "x"}',
    }


@pytest.mark.asyncio
async def test_two_interleaved_tool_calls_yield_in_index_order():
    body = _sse(
        _tool_call_line(
            [{"index": 0, "id": "call_a", "function": {"name": "a", "arguments": ""}}]
        ),
        _tool_call_line(
            [{"index": 1, "id": "call_b", "function": {"name": "b", "arguments": ""}}]
        ),
        _tool_call_line([{"index": 0, "function": {"arguments": '{"x"'}}]),
        _tool_call_line([{"index": 1, "function": {"arguments": '{"y"'}}]),
        _tool_call_line([{"index": 0, "function": {"arguments": ": 1}"}}]),
        _tool_call_line([{"index": 1, "function": {"arguments": ": 2}"}}]),
        "[DONE]",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        events = await _collect_events(
            stream_chat_events([{"role": "user", "content": "hi"}], CFG, client=client)
        )

    tool_calls = [e for e in events if e["type"] == "tool_call"]
    assert len(tool_calls) == 2
    assert tool_calls[0] == {
        "type": "tool_call",
        "id": "call_a",
        "name": "a",
        "arguments": '{"x": 1}',
    }
    assert tool_calls[1] == {
        "type": "tool_call",
        "id": "call_b",
        "name": "b",
        "arguments": '{"y": 2}',
    }


@pytest.mark.asyncio
async def test_content_delta_before_tool_call_both_arrive_delta_first():
    body = _sse(
        _tool_call_line(None, content="Let me check that."),
        _tool_call_line(
            [
                {
                    "index": 0,
                    "id": "call_1",
                    "function": {"name": "lookup", "arguments": '{"q": 1}'},
                }
            ]
        ),
        "[DONE]",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        events = await _collect_events(
            stream_chat_events([{"role": "user", "content": "hi"}], CFG, client=client)
        )

    assert len(events) == 2
    assert events[0] == {"type": "delta", "text": "Let me check that."}
    assert events[1] == {
        "type": "tool_call",
        "id": "call_1",
        "name": "lookup",
        "arguments": '{"q": 1}',
    }
