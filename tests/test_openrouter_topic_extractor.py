"""Tests for src/openrouter_topic_extractor.py."""
from __future__ import annotations

import json
from typing import Callable

import httpx
import pytest

from src.openrouter_topic_extractor import (
    OpenRouterTopicExtractorCLI,
    _extract_first_json_array,
    _parse_topics,
)


def make_extractor(handler: Callable[[httpx.Request], httpx.Response]) -> OpenRouterTopicExtractorCLI:
    return OpenRouterTopicExtractorCLI(
        api_key="sk-test",
        model="test/model",
        timeout=2.0,
        transport=httpx.MockTransport(handler),
    )


def _ok_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
    )


async def test_extract_topics_success() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return _ok_response('["a","b","c"]')

    extractor = make_extractor(handler)
    result = await extractor.extract_topics("what topics?")
    assert result == ["a", "b", "c"]
    assert extractor._consecutive_failures == 0


async def test_extract_topics_with_preamble() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return _ok_response('Sure, here you go: ["a","b"]')

    extractor = make_extractor(handler)
    result = await extractor.extract_topics("what topics?")
    assert result == ["a", "b"]


async def test_extract_topics_object_wrapped() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return _ok_response('{"topics":["a","b"]}')

    extractor = make_extractor(handler)
    result = await extractor.extract_topics("what topics?")
    assert result == ["a", "b"]


async def test_extract_topics_caps_at_5() -> None:
    seven_topics = json.dumps([f"t{i}" for i in range(7)])

    def handler(req: httpx.Request) -> httpx.Response:
        return _ok_response(seven_topics)

    extractor = make_extractor(handler)
    result = await extractor.extract_topics("what topics?")
    assert len(result) == 5


async def test_extract_topics_timeout() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    extractor = make_extractor(handler)
    result = await extractor.extract_topics("what topics?")
    assert result == []
    assert extractor._consecutive_failures == 1


async def test_extract_topics_http_500() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal")

    extractor = make_extractor(handler)
    result = await extractor.extract_topics("what topics?")
    assert result == []
    assert extractor._consecutive_failures == 1


async def test_extract_topics_invalid_json_content() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return _ok_response("not valid json at all")

    extractor = make_extractor(handler)
    result = await extractor.extract_topics("what topics?")
    assert result == []
    assert extractor._consecutive_failures == 1


async def test_extract_topics_consecutive_failures_warning(caplog: pytest.LogCaptureFixture) -> None:
    def timeout_handler(req: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    extractor = make_extractor(timeout_handler)

    with caplog.at_level("WARNING", logger="heare.openrouter_topic_extractor"):
        await extractor.extract_topics("q1")
        await extractor.extract_topics("q2")
        await extractor.extract_topics("q3")

    assert extractor._consecutive_failures == 3
    warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("consecutive_failures=3" in m for m in warning_messages)
    assert any("backend=openrouter" in m for m in warning_messages)

    # Now a successful call should reset the counter
    def ok_handler(req: httpx.Request) -> httpx.Response:
        return _ok_response('["reset"]')

    extractor._transport = httpx.MockTransport(ok_handler)
    result = await extractor.extract_topics("q4")
    assert result == ["reset"]
    assert extractor._consecutive_failures == 0


async def test_extract_topics_does_not_send_response_format() -> None:
    captured_body: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(req.content))
        return _ok_response('["x"]')

    extractor = make_extractor(handler)
    await extractor.extract_topics("test prompt")

    assert "response_format" not in captured_body
    assert captured_body["model"] == "test/model"
    assert captured_body["stream"] is False
    assert captured_body["messages"][0]["role"] == "user"


def test_parse_topics_strips_markdown_fence() -> None:
    result = _parse_topics('```json\n["a","b"]\n```')
    assert result == ["a", "b"]


def test_extract_first_json_array_basic() -> None:
    result = _extract_first_json_array('foo ["x","y"] bar')
    assert result == ["x", "y"]
