"""Tests for src/storage.py TranscriptStore against a temp SQLite db."""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import aiosqlite
import pytest

from src.store.storage import TranscriptStore


@pytest.fixture
async def store():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "heare.db"
        s = TranscriptStore(db)
        await s.init()
        try:
            yield s
        finally:
            await s.close()


async def test_log_and_read_transcript(store: TranscriptStore) -> None:
    now = time.time()
    cursor = await store.db.execute(
        "INSERT INTO transcripts (ts, text, mode) VALUES (?, ?, ?)",
        (now, "привіт", "ambient"),
    )
    await store.db.commit()
    assert cursor.lastrowid is not None

    recent = await store.recent_transcripts(5)
    assert len(recent) == 1
    assert recent[0]["text"] == "привіт"
    assert recent[0]["mode"] == "ambient"


async def test_recent_transcripts_ordering(store: TranscriptStore) -> None:
    now = time.time()
    for i, text in enumerate(["first", "second", "third"]):
        await store.db.execute(
            "INSERT INTO transcripts (ts, text, mode) VALUES (?, ?, ?)",
            (now + i, text, "ambient"),
        )
    await store.db.commit()

    recent = await store.recent_transcripts(2)
    assert [r["text"] for r in recent] == ["second", "third"]


async def test_recent_transcripts_empty_db(store: TranscriptStore) -> None:
    recent = await store.recent_transcripts(5)
    assert recent == []




async def test_schema_version_fresh_install(store: TranscriptStore) -> None:
    cursor = await store.db.execute(
        "SELECT value FROM meta WHERE key = ?", ("schema_version",)
    )
    row = await cursor.fetchone()
    assert row is not None
    from src.store.storage import SCHEMA_VERSION

    assert int(row[0]) == SCHEMA_VERSION


async def test_schema_version_fail_loud_on_newer_db() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "newer.db"
        s = TranscriptStore(db_path)
        await s.init()
        await s.db.execute(
            "UPDATE meta SET value = ? WHERE key = ?", ("99", "schema_version")
        )
        await s.db.commit()
        await s.close()

        s2 = TranscriptStore(db_path)
        with pytest.raises(RuntimeError, match=r"99"):
            await s2.init()
        await s2.close()


async def test_schema_version_current_on_fresh_install(store: TranscriptStore) -> None:
    from src.store.storage import SCHEMA_VERSION

    cursor = await store.db.execute(
        "SELECT value FROM meta WHERE key = ?", ("schema_version",)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert int(row[0]) == SCHEMA_VERSION


async def test_wal_mode_set_after_init(store: TranscriptStore) -> None:
    cursor = await store.db.execute("PRAGMA journal_mode")
    row = await cursor.fetchone()
    assert row is not None
    assert row[0].lower() == "wal"


async def test_foreign_keys_enabled_after_init(store: TranscriptStore) -> None:
    cursor = await store.db.execute("PRAGMA foreign_keys")
    row = await cursor.fetchone()
    assert row is not None
    assert int(row[0]) == 1


async def test_conversation_tables_create(store: TranscriptStore) -> None:
    """Verify that conversation and turns tables were created during init."""
    # Check conversations table exists
    cursor = await store.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='conversations'"
    )
    row = await cursor.fetchone()
    assert row is not None, "conversations table should exist"

    # Check turns table exists
    cursor = await store.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='turns'"
    )
    row = await cursor.fetchone()
    assert row is not None, "turns table should exist"

    # Check transcripts.turn_id column exists
    cursor = await store.db.execute("PRAGMA table_info(transcripts)")
    columns = await cursor.fetchall()
    column_names = [col[1] for col in columns]
    assert "turn_id" in column_names, "transcripts.turn_id column should exist"
# ============================================================================
# CCS-01: action_log persistence
# ============================================================================


async def test_actions_table_has_action_log_columns(store: TranscriptStore) -> None:
    cursor = await store.db.execute("PRAGMA table_info(actions)")
    rows = await cursor.fetchall()
    names = {row[1] for row in rows}
    assert "tool" in names
    assert "args" in names
    assert "result_json" in names
    assert "intent_id" in names


async def test_action_log_migration_idempotent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "heare.db"
        s1 = TranscriptStore(db)
        await s1.init()
        await s1.close()
        # Re-open the same DB; init() must be a noop on the additive columns.
        s2 = TranscriptStore(db)
        await s2.init()
        cursor = await s2.db.execute("PRAGMA table_info(actions)")
        rows = await cursor.fetchall()
        names = {row[1] for row in rows}
        assert {"tool", "args", "result_json", "intent_id"}.issubset(names)
        await s2.close()


async def test_upsert_action_log_entry_inserts_then_updates(
    store: TranscriptStore,
) -> None:
    import json

    rid1 = await store.upsert_action_log_entry(
        intent_id=42,
        tool="web_search",
        args="chili recipe",
        status="pending",
        result=None,
    )
    assert rid1 > 0
    rid2 = await store.upsert_action_log_entry(
        intent_id=42,
        tool="web_search",
        args="chili recipe",
        status="done",
        result=json.dumps({"summary": "found 5 hits"}),
    )
    assert rid2 == rid1, "UPSERT must reuse the same row id"
    cursor = await store.db.execute(
        "SELECT status, result_json, tool FROM actions WHERE intent_id = ?",
        (42,),
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "done"
    assert "summary" in rows[0][1]
    assert rows[0][2] == "web_search"


@pytest.mark.asyncio
async def test_log_transcript_records_source(store: TranscriptStore) -> None:
    now = time.time()
    await store.db.execute(
        "INSERT INTO transcripts (ts, text, mode, source) VALUES (?, ?, ?, ?)",
        (now, "spoken", "ambient", "voice"),
    )
    await store.db.execute(
        "INSERT INTO transcripts (ts, text, mode, source) VALUES (?, ?, ?, ?)",
        (now + 1, "typed", "ambient", "typed"),
    )
    await store.db.commit()

    cursor = await store.db.execute(
        "SELECT text, source FROM transcripts ORDER BY id"
    )
    assert await cursor.fetchall() == [("spoken", "voice"), ("typed", "typed")]


@pytest.mark.asyncio
async def test_log_transcript_source_defaults_to_null(store: TranscriptStore) -> None:
    """Callers that don't care leave it NULL; readers treat that as 'voice'."""
    now = time.time()
    await store.db.execute(
        "INSERT INTO transcripts (ts, text, mode) VALUES (?, ?, ?)",
        (now, "hello", "ambient"),
    )
    await store.db.commit()

    cursor = await store.db.execute("SELECT source FROM transcripts")
    assert (await cursor.fetchone())[0] is None


@pytest.mark.asyncio
async def test_source_migration_is_idempotent(tmp_path) -> None:
    """init() runs the ALTER every start — it must survive the second one, and
    a database predating the column must gain it rather than be rebuilt."""
    db_path = tmp_path / "heare.db"

    legacy = await aiosqlite.connect(db_path)
    await legacy.execute(
        "CREATE TABLE transcripts (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ts REAL NOT NULL, text TEXT NOT NULL, mode TEXT NOT NULL)"
    )
    await legacy.execute(
        "INSERT INTO transcripts (ts, text, mode) VALUES (1.0, 'old row', 'ambient')"
    )
    await legacy.commit()
    await legacy.close()

    for _ in range(2):
        store = TranscriptStore(db_path)
        await store.init()
        cursor = await store.db.execute("SELECT text, source FROM transcripts")
        assert await cursor.fetchall() == [("old row", None)]
        await store.close()
