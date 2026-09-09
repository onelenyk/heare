"""The cost of a call, from the provider's own mouth to the ledger.

Every number in `make day` and in the dashboard's usage card came from
multiplying token counts by a table copied off a pricing page by hand.
When that table lacked the model — which it did, for the only model in
daily use — the answer was zero, and zero is indistinguishable from a
free call at every point downstream.

OpenRouter will state what a completion actually cost. These tests pin
the path that carries that number: it has to be asked for, parsed,
carried on the usage event, and preferred over the catalog. And silence
from a provider that does not report cost has to stay silence.
"""
from __future__ import annotations

import json

import httpx
import pytest

from src.spine.llm import LLMConfig, _reported_cost, stream_chat_events


def _sse(*chunks: dict) -> bytes:
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
    return (body + "data: [DONE]\n\n").encode()


def _cfg(provider: str) -> LLMConfig:
    return LLMConfig(
        base_url="https://example.invalid/v1",
        api_key="k",
        model="deepseek/deepseek-v4-flash",
        provider=provider,
    )


class TestReportedCost:
    def test_absent_is_none(self) -> None:
        assert _reported_cost({"prompt_tokens": 10}) is None

    def test_zero_is_a_real_answer(self) -> None:
        """A free model costs 0.0 and says so. Only *silence* is None —
        collapsing the two is the bug this module exists to prevent."""
        assert _reported_cost({"cost": 0}) == 0.0

    def test_a_number_is_taken(self) -> None:
        assert _reported_cost({"cost": 0.00042}) == pytest.approx(0.00042)

    def test_a_string_number_is_taken(self) -> None:
        assert _reported_cost({"cost": "0.5"}) == 0.5

    def test_nonsense_is_none_not_zero(self) -> None:
        assert _reported_cost({"cost": "free!"}) is None

    def test_a_negative_bill_is_refused(self) -> None:
        """Not a refund — a provider bug. Летить у мінус денний підсумок."""
        assert _reported_cost({"cost": -1.0}) is None


class TestTheAskAndTheAnswer:
    @pytest.mark.asyncio
    async def test_openrouter_is_asked_for_cost(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, content=_sse(), headers={"content-type": "text/event-stream"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async for _ in stream_chat_events([], _cfg("openrouter"), client=client):
            pass
        await client.aclose()
        assert seen.get("usage") == {"include": True}, (
            "OpenRouter reports cost only when asked; unasked, the ledger "
            "falls back to a hand-copied price table"
        )

    @pytest.mark.asyncio
    async def test_other_providers_are_not_asked(self) -> None:
        """`usage: {include: true}` is an OpenRouter extension. Sending it
        to a provider that does not know it risks a 400 on every turn."""
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, content=_sse(), headers={"content-type": "text/event-stream"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async for _ in stream_chat_events([], _cfg("deepseek"), client=client):
            pass
        await client.aclose()
        assert "usage" not in seen

    @pytest.mark.asyncio
    async def test_the_cost_reaches_the_usage_event(self) -> None:
        chunk = {
            "model": "deepseek/deepseek-v4-flash",
            "choices": [],
            "usage": {
                "prompt_tokens": 1200,
                "completion_tokens": 80,
                "cost": 0.000117,
            },
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_sse(chunk), headers={"content-type": "text/event-stream"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        events = [
            e async for e in stream_chat_events([], _cfg("openrouter"), client=client)
        ]
        await client.aclose()

        usage = [e for e in events if e["type"] == "usage"]
        assert len(usage) == 1
        assert usage[0]["cost_usd"] == pytest.approx(0.000117)
        assert usage[0]["provider"] == "openrouter"
        assert usage[0]["input_tokens"] == 1200
        assert usage[0]["output_tokens"] == 80

    @pytest.mark.asyncio
    async def test_a_silent_provider_yields_no_cost(self) -> None:
        chunk = {
            "model": "deepseek-v4-flash",
            "choices": [],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_sse(chunk), headers={"content-type": "text/event-stream"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        events = [
            e async for e in stream_chat_events([], _cfg("deepseek"), client=client)
        ]
        await client.aclose()

        usage = [e for e in events if e["type"] == "usage"][0]
        assert usage["cost_usd"] is None
        assert usage["provider"] == "deepseek"


class TestEndToEndIntoTheLedger:
    @pytest.mark.asyncio
    async def test_a_reported_cost_lands_in_the_database(self, tmp_path) -> None:
        """The seam this fix is really about: what the provider charged
        has to survive all the way into usage_events, not be recomputed."""
        from src.spine.usage import SpineUsage

        chunk = {
            "model": "qwen/qwen3.7-flash",
            "choices": [],
            "usage": {
                "prompt_tokens": 900,
                "completion_tokens": 40,
                "cost": 0.0000322,
            },
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_sse(chunk), headers={"content-type": "text/event-stream"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        usage = SpineUsage(tmp_path / "u.db")
        async for ev in stream_chat_events([], _cfg("openrouter"), client=client):
            if ev["type"] == "usage":
                usage.llm(
                    ev["model"],
                    ev["input_tokens"],
                    ev["output_tokens"],
                    provider=ev["provider"],
                    cost_usd=ev["cost_usd"],
                )
        await client.aclose()

        row = usage._db.execute(
            "SELECT provider, model, cost_usd FROM usage_events WHERE kind='llm'"
        ).fetchone()
        usage.close()
        assert row == ("openrouter", "qwen/qwen3.7-flash", pytest.approx(0.0000322))
