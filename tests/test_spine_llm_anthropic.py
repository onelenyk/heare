"""The other protocol, exercised without a key.

Z.AI answers Anthropic's wire format, not OpenAI's. Until now the engine
had one shape of request and one shape of parser, so selecting Z.AI would
have sent an OpenAI body to an Anthropic endpoint — a 400, or worse, a
silent misread.

No live call is possible here: there is no Z.AI key on this machine. What
these fixtures pin is everything that is ours — what we send, and how we
read what comes back. The handshake itself remains unverified, and that
is worth saying out loud rather than discovering by voice.
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.spine.llm import LLMConfig, _to_anthropic, stream_chat_events

CFG = LLMConfig(
    base_url="https://api.z.ai/api/anthropic",
    api_key="sk-zai",
    model="claude-3-5-sonnet",
    provider="zai",
    api_style="anthropic",
)


def _sse(*chunks: dict) -> bytes:
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks).encode()


def _capture():
    """A transport that answers a fixed stream and keeps the request."""
    seen: dict = {}

    def make(*chunks: dict):
        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["headers"] = dict(request.headers)
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=_sse(*chunks),
            )

        return httpx.MockTransport(handler)

    return seen, make


async def _collect(gen) -> list[dict]:
    return [event async for event in gen]


# ── what we send ──────────────────────────────────────────────────────


def test_the_system_prompt_stops_being_a_message() -> None:
    """Anthropic takes it as a top-level field. Left in the message list it
    is either rejected or silently treated as something the user said."""
    system, turns, _ = _to_anthropic(
        [
            {"role": "system", "content": "Ти heare."},
            {"role": "user", "content": "привіт"},
        ],
        None,
    )
    assert system == "Ти heare."
    assert turns == [{"role": "user", "content": "привіт"}]


def test_two_turns_in_a_row_from_the_same_side_are_joined() -> None:
    """The protocol rejects them outright, and the spine produces them —
    an unprompted remark from the engine lands beside a spoken reply."""
    _, turns, _ = _to_anthropic(
        [
            {"role": "assistant", "content": "перевірка скінчилась"},
            {"role": "assistant", "content": "все гаразд"},
            {"role": "user", "content": "добре"},
        ],
        None,
    )
    assert [t["role"] for t in turns] == ["assistant", "user"]
    assert turns[0]["content"] == "перевірка скінчилась\n\nвсе гаразд"


def test_a_tool_keeps_its_schema_under_the_other_name() -> None:
    _, _, declared = _to_anthropic(
        [],
        [
            {
                "type": "function",
                "function": {
                    "name": "remember",
                    "description": "запамʼятати",
                    "parameters": {"type": "object", "properties": {"text": {}}},
                },
            }
        ],
    )
    assert declared == [
        {
            "name": "remember",
            "description": "запамʼятати",
            "input_schema": {"type": "object", "properties": {"text": {}}},
        }
    ]


@pytest.mark.asyncio
async def test_the_request_goes_where_anthropic_listens() -> None:
    seen, make = _capture()
    async with httpx.AsyncClient(transport=make({"type": "message_stop"})) as client:
        await _collect(
            stream_chat_events(
                [{"role": "user", "content": "привіт"}], CFG, client=client
            )
        )

    assert seen["url"] == "https://api.z.ai/api/anthropic/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-zai"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    # required by the protocol, with no default of its own
    assert seen["body"]["max_tokens"] > 0
    assert "authorization" not in seen["headers"]


# ── what we read ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_text_arrives_as_the_same_delta_events() -> None:
    """Everything downstream — the sentence splitter, the mouth, the usage
    ledger — was written against one event shape. The protocol must not
    reach any of it."""
    _, make = _capture()
    chunks = (
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "При"},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "віт."},
        },
        {"type": "message_stop"},
    )
    async with httpx.AsyncClient(transport=make(*chunks)) as client:
        events = await _collect(
            stream_chat_events([{"role": "user", "content": "hi"}], CFG, client=client)
        )

    assert [e["text"] for e in events if e["type"] == "delta"] == ["При", "віт."]


@pytest.mark.asyncio
async def test_a_tool_call_is_assembled_from_its_fragments() -> None:
    _, make = _capture()
    chunks = (
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "toolu_1", "name": "delegate"},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"task":'},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": ' "перевір диск"}'},
        },
        {"type": "message_stop"},
    )
    async with httpx.AsyncClient(transport=make(*chunks)) as client:
        events = await _collect(
            stream_chat_events(
                [{"role": "user", "content": "hi"}], CFG, tools=[], client=client
            )
        )

    calls = [e for e in events if e["type"] == "tool_call"]
    assert len(calls) == 1
    assert calls[0]["name"] == "delegate"
    assert json.loads(calls[0]["arguments"]) == {"task": "перевір диск"}


@pytest.mark.asyncio
async def test_a_tool_called_with_nothing_still_parses() -> None:
    """No input_json_delta arrives for a no-argument tool. The caller runs
    json.loads on this string, and "" is not JSON."""
    _, make = _capture()
    chunks = (
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "t1", "name": "recall"},
        },
        {"type": "message_stop"},
    )
    async with httpx.AsyncClient(transport=make(*chunks)) as client:
        events = await _collect(
            stream_chat_events(
                [{"role": "user", "content": "hi"}], CFG, tools=[], client=client
            )
        )

    assert json.loads([e for e in events if e["type"] == "tool_call"][0]["arguments"]) == {}


@pytest.mark.asyncio
async def test_the_token_count_still_reaches_the_ledger() -> None:
    """Anthropic splits it in two: input at the start, output at the end.
    Read only one and every conversation looks half as expensive."""
    _, make = _capture()
    chunks = (
        {
            "type": "message_start",
            "message": {"model": "claude-3-5-sonnet", "usage": {"input_tokens": 120}},
        },
        {"type": "message_delta", "usage": {"output_tokens": 40}},
        {"type": "message_stop"},
    )
    async with httpx.AsyncClient(transport=make(*chunks)) as client:
        events = await _collect(
            stream_chat_events([{"role": "user", "content": "hi"}], CFG, client=client)
        )

    usage = [e for e in events if e["type"] == "usage"]
    assert usage == [
        {
            "type": "usage",
            "model": "claude-3-5-sonnet",
            "input_tokens": 120,
            "output_tokens": 40,
        }
    ]
