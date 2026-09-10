"""The worker's model calls go in the ledger.

Until 10 September they did not. Every response from the provider carries
a ``usage`` block; ``Hands._chat`` took ``choices[0].message`` and threw
the envelope away. The voice path's calls were counted, the worker's were
not — up to twelve per delegated job, each prompt carrying the whole tool
list. On this machine: 28 delegations and 69 tool steps between 10 and 21
August, on the order of a hundred calls the usage card never saw.

Driven through an httpx MockTransport, so the request the worker actually
builds is the one under test and no network is touched.
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from src.agent.hands import Hands
from src.features import usage as usage_feature
from src.features.usage import UsagePlugin
from src.plugins import WireContext


def _settings(tmp_path, provider="deepseek"):
    return SimpleNamespace(
        db_path=tmp_path / "h.db",
        workspace_dir=tmp_path / "ws",
        deepseek_api_key="sk-test" if provider == "deepseek" else None,
        openrouter_api_key="sk-or-test" if provider == "openrouter" else None,
        zai_api_key=None,
        opencode_api_key=None,
        llm_provider=provider,
        groq_language="uk",
    )


@pytest.fixture
def plugin(monkeypatch):
    fresh = UsagePlugin()
    monkeypatch.setattr(usage_feature, "PLUGIN", fresh)
    yield fresh
    if fresh._ledger is not None:
        fresh._ledger.close()


def _transport(seen: list[dict], usage: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "Готово."}}],
                "usage": usage,
            },
        )

    return httpx.MockTransport(handler)


def _rows(db):
    with sqlite3.connect(db) as con:
        return con.execute(
            "SELECT provider, model, input_tokens, output_tokens, cost_usd "
            "FROM usage_events WHERE kind = 'llm'"
        ).fetchall()


async def _one_call(settings, seen, usage):
    worker = Hands(settings)
    async with httpx.AsyncClient(transport=_transport(seen, usage)) as client:
        return await worker._chat(client, [{"role": "user", "content": "x"}], [])


async def test_a_worker_call_is_written_down(plugin, tmp_path) -> None:
    settings = _settings(tmp_path)
    plugin.wire(WireContext(settings, True))
    seen: list[dict] = []

    reply = await _one_call(
        settings, seen, {"prompt_tokens": 4100, "completion_tokens": 60}
    )

    assert reply["content"] == "Готово."
    [(provider, model, tokens_in, tokens_out, _)] = _rows(settings.db_path)
    assert (provider, tokens_in, tokens_out) == ("deepseek", 4100, 60)
    assert model == seen[0]["model"]


async def test_openrouter_is_asked_for_the_price_and_the_price_is_kept(plugin, tmp_path) -> None:
    """A stated cost beats one multiplied out of a hand-copied table."""
    settings = _settings(tmp_path, provider="openrouter")
    plugin.wire(WireContext(settings, True))
    seen: list[dict] = []

    await _one_call(
        settings, seen, {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.00042}
    )

    assert seen[0]["usage"] == {"include": True}
    [(provider, *_, cost)] = _rows(settings.db_path)
    assert (provider, cost) == ("openrouter", pytest.approx(0.00042))


async def test_switched_off_the_worker_counts_nothing(plugin, tmp_path) -> None:
    settings = _settings(tmp_path)
    plugin.wire(WireContext(settings, False))

    await _one_call(settings, [], {"prompt_tokens": 1, "completion_tokens": 1})

    assert not settings.db_path.exists() or _rows(settings.db_path) == []


async def test_a_broken_ledger_does_not_break_the_job(plugin, tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    plugin.wire(WireContext(settings, True))

    def _boom(**_kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(plugin._ledger, "llm", _boom)

    reply = await _one_call(settings, [], {"prompt_tokens": 1, "completion_tokens": 1})

    assert reply["content"] == "Готово."
