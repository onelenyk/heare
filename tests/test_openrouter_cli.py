"""Tests for OpenRouterCLI streaming client."""
from __future__ import annotations

import json

import httpx
import pytest

from src.openrouter_cli import OpenRouterCLI, OpenRouterError


def _sse_bytes(chunks: list[str], finish: bool = True) -> bytes:
    """Build an OpenAI-compatible SSE stream from text deltas."""
    lines: list[str] = []
    for c in chunks:
        event = {
            "id": "x",
            "object": "chat.completion.chunk",
            "model": "test",
            "choices": [{"index": 0, "delta": {"content": c}, "finish_reason": None}],
        }
        lines.append("data: " + json.dumps(event))
        lines.append("")
    if finish:
        lines.append("data: [DONE]")
        lines.append("")
    return ("\n".join(lines) + "\n").encode()


def _cli(handler, api_key: str = "k", model: str = "m") -> OpenRouterCLI:
    return OpenRouterCLI(
        api_key=api_key, model=model, transport=httpx.MockTransport(handler)
    )


async def _collect(cli: OpenRouterCLI, prompt: str, system: str | None = None) -> list[str]:
    out: list[str] = []
    async for chunk in cli.generate(prompt, system=system):
        out.append(chunk)
    return out


async def test_streaming_yields_text_chunks():
    captured_body: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return httpx.Response(200, content=_sse_bytes(["Привіт", ", ", "друже!"]))

    cli = _cli(handler)
    chunks = await _collect(cli, "hi")

    assert chunks == ["Привіт", ", ", "друже!"]
    assert captured_body["model"] == "m"
    assert captured_body["stream"] is True
    assert captured_body["messages"][-1] == {"role": "user", "content": "hi"}


async def test_system_prompt_prepended_when_provided():
    captured_body: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return httpx.Response(200, content=_sse_bytes(["hi"]))

    cli = _cli(handler)
    await _collect(cli, "hello", system="you are heare")

    assert captured_body["messages"][0] == {"role": "system", "content": "you are heare"}
    assert captured_body["messages"][1] == {"role": "user", "content": "hello"}


async def test_non_2xx_raises_openrouter_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text='{"error":"rate limit"}')

    cli = _cli(handler)
    with pytest.raises(OpenRouterError) as exc_info:
        await _collect(cli, "hi")

    assert "429" in str(exc_info.value)


async def test_done_marker_stops_iteration():
    def handler(request: httpx.Request) -> httpx.Response:
        body = _sse_bytes(["one", "two"], finish=True) + b"data: {\"should\":\"not appear\"}\n\n"
        return httpx.Response(200, content=body)

    cli = _cli(handler)
    chunks = await _collect(cli, "hi")

    assert chunks == ["one", "two"]


async def test_transport_error_raises_openrouter_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated failure")

    cli = _cli(handler)
    with pytest.raises(OpenRouterError) as exc_info:
        await _collect(cli, "hi")

    assert "transport" in str(exc_info.value).lower()


async def test_empty_delta_chunks_are_skipped():
    def handler(request: httpx.Request) -> httpx.Response:
        events = [
            {"choices": [{"delta": {"content": "good"}}]},
            {"choices": [{"delta": {}}]},
            {"choices": [{"delta": {"content": ""}}]},
            {"choices": [{"delta": {"content": " morning"}}]},
        ]
        body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
        return httpx.Response(200, content=body.encode())

    cli = _cli(handler)
    chunks = await _collect(cli, "hi")

    assert chunks == ["good", " morning"]


async def test_auth_header_and_referer_included():
    captured_headers: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.update(request.headers)
        return httpx.Response(200, content=_sse_bytes(["ok"]))

    cli = _cli(handler, api_key="sk-or-abc")
    await _collect(cli, "hi")

    assert captured_headers.get("authorization") == "Bearer sk-or-abc"
    assert captured_headers.get("http-referer") == "https://github.com/onelenyk/heare"
