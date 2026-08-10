"""Memory search must return the best matches first.

For months it returned the worst. Two independent sign errors in one
ORDER BY clause, and no test caught either, because every test asserted
that results *exist* rather than that they are the right ones.

See docs/findings/known-broken.md.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest
import pytest_asyncio

from src.memory.base import MemoryEntry, MemoryType
from src.memory.sqlite_backend import SQLiteBackend

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def backend():
    with tempfile.TemporaryDirectory() as tmp:
        b = SQLiteBackend(db_path=Path(tmp) / "memory.db")
        await b.initialize()
        yield b
        await b.close()


async def _store(backend: SQLiteBackend, content: str, **kw) -> str:
    """Store a memory that has been recalled before.

    ``last_accessed`` defaults to 0 on a freshly stored row and only
    becomes a real timestamp once ``context()`` has retrieved it. That
    detail decides everything here: at 0 the broken ranking expression
    stayed positive and behaved almost sensibly, and it inverted only
    for memories that had actually been used. Tests that stored rows and
    searched immediately therefore never saw the bug — which is why it
    survived. These set the timestamp on purpose.
    """
    kw.setdefault("last_accessed", time.time())
    return await backend.store(
        MemoryEntry(
            id="",
            type=MemoryType.FACT,
            content=content,
            created_ts=kw.pop("created_ts", time.time()),
            **kw,
        )
    )


async def test_strongest_match_comes_first(backend: SQLiteBackend) -> None:
    """The whole point of ranking, and it used to be exactly inverted.

    The clause read ``strftime('%%s','now')``. Built with an f-string,
    that reaches SQLite as the literal ``%s``, which coerces to 0 — so
    the multiplier came out near -206 instead of ~1 and flipped the sign
    of every score.

    All three rows below MATCH, so FTS cannot do the filtering: the order
    is decided by ranking alone. Against the shipped clause this exact
    list comes back reversed.
    """
    await _store(backend, "kayak kayak kayak kayak")
    await _store(backend, "kayak")
    await _store(backend, "kayak and a river and a paddle and a lake and a boat")

    results = await backend.search("kayak", limit=3)

    assert len(results) == 3
    assert results[0].content == "kayak kayak kayak kayak"
    assert results[-1].content.startswith("kayak and a river")


async def test_recent_access_lifts_a_memory_rather_than_burying_it(
    backend: SQLiteBackend,
) -> None:
    """FTS5's rank is negative and sorted ASC, so a boost must make the
    product *more* negative. The terms used to subtract, which demoted
    every memory they were meant to promote."""
    old = await _store(
        backend,
        "kayak trip on the river",
        last_accessed=time.time() - 90 * 24 * 3600,
    )
    recent = await _store(
        backend,
        "kayak trip on the lake",
        last_accessed=time.time(),
    )

    results = await backend.search("kayak", limit=2)
    ids = [entry.id for entry in results]

    assert set(ids) == {old, recent}
    assert ids[0] == recent, "the recently used memory should rank first"


async def test_limit_keeps_the_best_rather_than_the_worst(
    backend: SQLiteBackend,
) -> None:
    """The failure users actually felt.

    ``recall`` asks for five and the model sees only those five. With the
    order inverted, a limit does not trim the tail — it throws away
    everything worth reading and keeps the dregs.
    """
    await _store(backend, "vault vault vault vault")
    for i in range(6):
        await _store(
            backend,
            f"note {i} mentions the vault once among many other unrelated words",
        )

    results = await backend.search("vault", limit=2)

    assert len(results) == 2
    assert results[0].content == "vault vault vault vault"


async def test_a_used_memory_is_not_buried_under_an_unused_one(
    backend: SQLiteBackend,
) -> None:
    """The sharpest form of the shipped bug.

    A never-recalled memory has ``last_accessed = 0``, which kept the
    broken multiplier positive (~0.65). A memory recalled even once has a
    real timestamp, which made it about -206. Scores of opposite sign
    sort into two blocks, so every used memory landed below every unused
    one — no matter how well it matched.
    """
    used = await _store(backend, "harbour harbour harbour harbour")
    await _store(backend, "a harbour mentioned once", last_accessed=0.0)

    results = await backend.search("harbour", limit=2)

    assert results[0].id == used


async def test_type_filter_still_applies_after_ranking(
    backend: SQLiteBackend,
) -> None:
    await _store(backend, "sailing is a hobby")
    await backend.store(
        MemoryEntry(
            id="",
            type=MemoryType.PREFERENCE,
            content="sailing on weekends is preferred",
            created_ts=time.time(),
        )
    )

    results = await backend.search("sailing", limit=5, types=[MemoryType.PREFERENCE])

    assert len(results) == 1
    assert results[0].type is MemoryType.PREFERENCE
