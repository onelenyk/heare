"""Tests for SQLiteBackend against the MemoryBackend ABC contract."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

from src.memory.base import MemoryEntry, MemoryType
from src.memory.sqlite_backend import SQLiteBackend


@pytest.fixture
async def backend():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_memory.db"
        b = SQLiteBackend(db_path)
        await b.initialize()
        try:
            yield b
        finally:
            await b.close()


async def test_store_and_search_roundtrip(backend: SQLiteBackend) -> None:
    entry = MemoryEntry(
        id="",
        type=MemoryType.FACT,
        content="User prefers dark mode",
        created_ts=time.time(),
    )
    mid = await backend.store(entry)
    assert mid

    results = await backend.search("dark mode")
    assert len(results) == 1
    assert results[0].content == "User prefers dark mode"
    assert results[0].type == MemoryType.FACT


async def test_search_type_filter(backend: SQLiteBackend) -> None:
    await backend.store(
        MemoryEntry(id="", type=MemoryType.FACT, content="The sky is blue", created_ts=time.time())
    )
    await backend.store(
        MemoryEntry(
            id="", type=MemoryType.PREFERENCE, content="I like dark mode", created_ts=time.time()
        )
    )

    results = await backend.search("dark", types=[MemoryType.PREFERENCE])
    assert len(results) == 1
    assert results[0].type == MemoryType.PREFERENCE
    assert results[0].content == "I like dark mode"


async def test_forget(backend: SQLiteBackend) -> None:
    entry = MemoryEntry(
        id="", type=MemoryType.FACT, content="temp fact", created_ts=time.time()
    )
    mid = await backend.store(entry)

    removed = await backend.forget(mid)
    assert removed is True

    results = await backend.search("temp")
    assert len(results) == 0

    stats = await backend.stats()
    assert stats["archived"] == 1
    assert stats["total"] == 1


async def test_context_updates_access(backend: SQLiteBackend) -> None:
    entry = MemoryEntry(
        id="", type=MemoryType.FACT, content="frequently accessed", created_ts=time.time()
    )
    mid = await backend.store(entry)

    cursor = await backend._db.execute(
        "SELECT access_count, last_accessed FROM memories WHERE id = ?", (mid,)
    )
    row = await cursor.fetchone()
    assert row[0] == 0
    assert row[1] == 0.0

    results = await backend.context()
    assert len(results) == 1
    assert results[0].last_accessed > 0

    cursor = await backend._db.execute(
        "SELECT access_count, last_accessed FROM memories WHERE id = ?", (mid,)
    )
    row = await cursor.fetchone()
    assert row[0] == 1
    assert row[1] > 0


async def test_stats(backend: SQLiteBackend) -> None:
    now = time.time()
    for _ in range(3):
        await backend.store(
            MemoryEntry(id="", type=MemoryType.FACT, content="fact", created_ts=now)
        )
    for _ in range(2):
        await backend.store(
            MemoryEntry(id="", type=MemoryType.PREFERENCE, content="pref", created_ts=now)
        )

    all_entries = await backend.search("fact", limit=10)
    await backend.forget(all_entries[0].id)

    stats = await backend.stats()
    assert stats["total"] == 5
    assert stats["archived"] == 1
    assert stats["by_type"] == {"fact": 3, "preference": 2}


async def test_initialize_idempotent(backend: SQLiteBackend) -> None:
    await backend.initialize()


async def test_search_fts5_ranking(backend: SQLiteBackend) -> None:
    now = time.time()
    await backend.store(
        MemoryEntry(
            id="", type=MemoryType.FACT, content="dark mode preference", created_ts=now
        )
    )
    await backend.store(
        MemoryEntry(
            id="", type=MemoryType.FACT, content="unrelated text", created_ts=now
        )
    )

    results = await backend.search("dark")
    assert len(results) == 1
    assert results[0].content == "dark mode preference"


async def test_store_autogenerates_id(backend: SQLiteBackend) -> None:
    entry = MemoryEntry(
        id="", type=MemoryType.FACT, content="auto-id test", created_ts=time.time()
    )
    mid = await backend.store(entry)
    assert mid
    assert len(mid) == 32


async def test_context_with_query_delegates_to_search(backend: SQLiteBackend) -> None:
    await backend.store(
        MemoryEntry(
            id="", type=MemoryType.FACT, content="query test", created_ts=time.time()
        )
    )
    await backend.store(
        MemoryEntry(
            id="", type=MemoryType.FACT, content="other thing", created_ts=time.time()
        )
    )

    results = await backend.context(query="query")
    assert len(results) == 1
    assert results[0].content == "query test"


async def test_forget_nonexistent(backend: SQLiteBackend) -> None:
    removed = await backend.forget("nonexistent-id")
    assert removed is False


# --- Natural-language recall (live defect: search() AND-ed every token,
# so filler words in a spoken question -- "яке", "я", "тобі", "казав" --
# made the whole query fail to match anything). ---------------------------


async def test_natural_language_query_finds_precise_content(
    backend: SQLiteBackend,
) -> None:
    """The exact live repro: a spoken question full of filler words must
    still find a memory that only contains the content words."""
    await backend.store(
        MemoryEntry(
            id="",
            type=MemoryType.FACT,
            content="Кодове слово користувача — жираф",
            created_ts=time.time(),
        )
    )

    results = await backend.search("яке кодове слово я тобі казав?")

    assert len(results) == 1
    assert results[0].content == "Кодове слово користувача — жираф"


async def test_precise_short_query_still_ranks_the_match_first(
    backend: SQLiteBackend,
) -> None:
    """A short, precise query must not regress: both of its tokens survive
    stopword filtering, so it should still behave like an AND-ish match
    and outrank unrelated decoys."""
    await backend.store(
        MemoryEntry(
            id="",
            type=MemoryType.FACT,
            content="Кодове слово користувача — жираф",
            created_ts=time.time(),
        )
    )
    await backend.store(
        MemoryEntry(id="", type=MemoryType.FACT, content="Улюблений колір — синій", created_ts=time.time())
    )
    await backend.store(
        MemoryEntry(id="", type=MemoryType.FACT, content="слово дня — дощ", created_ts=time.time())
    )

    results = await backend.search("кодове слово")

    assert results[0].content == "Кодове слово користувача — жираф"


async def test_all_stopword_query_falls_back_without_raising(
    backend: SQLiteBackend,
) -> None:
    """A query made entirely of function words has nothing left to search
    on after filtering. It must not raise, and must return the documented
    fallback: the most recently created memories."""
    await backend.store(
        MemoryEntry(id="", type=MemoryType.FACT, content="a fact stored earlier", created_ts=time.time())
    )

    results = await backend.search("а що це є?")

    assert results == await backend._get_recent(5)


async def test_natural_language_query_matches_on_content_word(
    backend: SQLiteBackend,
) -> None:
    """Mixed function/content-word English query: only the content word
    ("editor") is expected to drive the match. Cross-language matching is
    not expected or tested here."""
    await backend.store(
        MemoryEntry(id="", type=MemoryType.FACT, content="Favorite editor is vim", created_ts=time.time())
    )
    await backend.store(
        MemoryEntry(id="", type=MemoryType.FACT, content="unrelated memory", created_ts=time.time())
    )

    results = await backend.search("what editor do I use")

    assert len(results) == 1
    assert results[0].content == "Favorite editor is vim"
