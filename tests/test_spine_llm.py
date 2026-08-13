"""Tests for src/spine/llm.py — no network, httpx.MockTransport only."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from src.spine.llm import LLMConfig, resolve_llm, stream_chat


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
