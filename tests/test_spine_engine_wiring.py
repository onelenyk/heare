"""The engine and the conductor, joined.

The unit tests prove the judgement; these prove the two ends are actually
connected — that a decision to speak reaches a mouth, and that what the
user says next reaches the engine. Both are the kind of wiring that is
easy to get subtly wrong and impossible to notice: an engine whose
remarks go nowhere looks exactly like an engine with nothing to say.
"""

from __future__ import annotations


import pytest

from src.spine import intents as I
from src.spine.engine import TRUST_MIN, Engine
from src.spine.loop import SpineLoop

pytestmark = pytest.mark.asyncio

NOW = 1_000_000.0


class _Store:
    def __init__(self, items=None):
        self.items = list(items or [])
        self.voiced: list[int] = []
        self.settled: list[tuple[int, str]] = []

    async def pending(self, limit: int = 10):
        return self.items[:limit]

    async def mark_voiced(self, intent_id):
        self.voiced.append(intent_id)
        self.items = [i for i in self.items if i.id != intent_id]

    async def settle(self, intent_id, outcome):
        self.settled.append((intent_id, outcome))

    async def drop(self, intent_id, reason=""):
        self.items = [i for i in self.items if i.id != intent_id]

    async def add(self, *a, **kw):
        return None


class _Persist:
    def last_turn_times(self):
        return {"any": NOW - 60, "user": NOW - 60}


def _intent(**kw) -> I.Intent:
    base = dict(
        id=1,
        kind="job_done",
        text="перевірка диска скінчилась",
        origin=I.USER,
        urgency=0.8,
        state=I.PENDING,
        created_ts=NOW - 300,
        updated_ts=NOW - 300,
    )
    base.update(kw)
    return I.Intent(**base)


async def test_a_decision_to_speak_reaches_the_conductor() -> None:
    """No second mouth: it goes in as a turn, so the model phrases it in
    voice and the reply is spoken by the same path as any other."""
    injected: list[str] = []

    async def inject(text: str) -> None:
        injected.append(text)

    engine = Engine(
        store=_Store([_intent()]), say=inject, persist=_Persist()
    )

    verdict = await engine.tick(now=NOW)

    assert verdict.speak is True
    assert injected == ["перевірка диска скінчилась"]


async def test_the_conductor_hands_every_reply_back() -> None:
    """The safeguard only works if the engine hears what came next."""
    seen: list[str] = []

    class _Engine:
        async def observe_reply(self, text: str) -> None:
            seen.append(text)

    loop = SpineLoop(
        audio=None,
        vad=None,
        assembler=None,
        transcribe=lambda pcm: None,
        stream_chat=lambda m: _empty(),
        split_sentences=lambda s: s,
        synthesise=lambda t: _empty(),
    )
    loop.engine = _Engine()

    await loop.respond("а що там з диском", speak=False)

    assert seen == ["а що там з диском"]


async def test_a_broken_engine_cannot_stop_the_conversation() -> None:
    """It is an addition. Its worst failure must be a quiet assistant,
    never a silent one."""

    class _Engine:
        async def observe_reply(self, text: str) -> None:
            raise RuntimeError("engine on fire")

    loop = SpineLoop(
        audio=None,
        vad=None,
        assembler=None,
        transcribe=lambda pcm: None,
        stream_chat=lambda m: _one("Відповідь."),
        split_sentences=_passthrough,
        synthesise=lambda t: _empty(),
    )
    loop.engine = _Engine()

    reply = await loop.respond("привіт", speak=False)

    assert reply == "Відповідь."


async def test_the_round_trip_costs_the_engine_its_patience() -> None:
    """Speak, be talked past, wait longer next time — end to end."""
    store = _Store([_intent()])
    injected: list[str] = []

    async def inject(text: str) -> None:
        injected.append(text)

    engine = Engine(store=store, say=inject, persist=_Persist())

    await engine.tick(now=NOW)
    assert injected and store.voiced == [1]

    await engine.observe_reply("яка завтра погода")

    assert store.settled == [(1, I.IGNORED)]
    assert engine.engine_state.trust > TRUST_MIN


async def _empty():
    return
    yield  # pragma: no cover


async def _one(text: str):
    yield text


async def _passthrough(stream):
    async for chunk in stream:
        yield chunk
