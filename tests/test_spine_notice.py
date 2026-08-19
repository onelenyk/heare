"""Events reach the person through the engine, not through a banner.

There used to be a notification subsystem: a facade, three backends, mode
gating, quiet hours, per-kind cooldowns — 835 lines deciding when a banner
might appear. It never appeared. The one call that assembled it lived in
the composition root of an engine that was deleted, and nothing replaced
it, so every producer silently got `None` for months while 954 lines of
tests stayed green around it.

Its questions were all real ones — is now a good time, is it night, have
we said this already. They just have better answers one layer up, where
something already knows whether you are mid-sentence and what it has
already been forward about today.

So an event is an intent now. These pin the two ends of that: that the
seam holds what it is given, and that a broken engine cannot turn a
disconnected MCP server into a crash.
"""

from __future__ import annotations

import pytest

from src.spine import intents as I
from src.spine.engine import Engine

pytestmark = pytest.mark.asyncio


class _Store:
    def __init__(self) -> None:
        self.added: list[tuple] = []
        self.exploding = False

    async def add(self, kind, text, *, origin=I.SELF, urgency=0.5, dedupe_key=None):
        if self.exploding:
            raise RuntimeError("disk full")
        self.added.append((kind, text, origin, urgency, dedupe_key))
        return len(self.added)

    async def pending(self, limit: int = 10):
        return []


def _engine(store: _Store) -> Engine:
    async def say(_text: str) -> None:  # pragma: no cover — not exercised here
        return None

    return Engine(store=store, say=say)


async def test_an_event_becomes_something_it_might_say() -> None:
    store = _Store()

    await _engine(store).notice("mcp_failed", "не піднялись інструменти: files")

    assert store.added == [
        ("mcp_failed", "не піднялись інструменти: files", I.SELF, 0.5, None)
    ]


async def test_who_asked_for_it_travels_with_it() -> None:
    """`origin` is what lets the judge treat "you asked for this" and "I
    thought of this" differently — at night only the first gets through."""
    store = _Store()

    await _engine(store).notice("mcp_failed", "впало", origin=I.USER, urgency=0.7)

    _, _, origin, urgency, _ = store.added[0]
    assert origin == I.USER
    assert urgency == 0.7


async def test_the_same_event_twice_is_still_one_thing_to_say() -> None:
    """The long-running beacon fires repeatedly for one piece of work.
    Without a dedupe key each beat would be another remark to make."""
    store = _Store()
    engine = _engine(store)

    for _ in range(3):
        await engine.notice("working", "ще працюю: пошук", dedupe_key="working:пошук")

    assert {row[4] for row in store.added} == {"working:пошук"}
    assert len(store.added) == 3  # the store dedupes; the seam does not guess


async def test_reporting_trouble_cannot_cause_more_trouble() -> None:
    """Every caller here is already on a failure path — an MCP server that
    would not start, a job that fell over. Raising into them would turn a
    missing tool into a dead daemon."""
    store = _Store()
    store.exploding = True

    await _engine(store).notice("mcp_failed", "впало")  # must not raise

    assert store.added == []
