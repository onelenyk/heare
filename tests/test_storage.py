"""Tests for src/storage.py TranscriptStore against a temp SQLite db."""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

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
    tid = await store.log_transcript("привіт", "ambient")
    assert tid > 0
    recent = await store.recent_transcripts(5)
    assert len(recent) == 1
    assert recent[0]["text"] == "привіт"
    assert recent[0]["mode"] == "ambient"


async def test_recent_transcripts_ordering(store: TranscriptStore) -> None:
    await store.log_transcript("first", "ambient")
    await store.log_transcript("second", "ambient")
    await store.log_transcript("third", "ambient")
    recent = await store.recent_transcripts(2)
    assert [r["text"] for r in recent] == ["second", "third"]


async def test_recent_transcripts_empty_db(store: TranscriptStore) -> None:
    recent = await store.recent_transcripts(5)
    assert recent == []


async def test_log_transcript_returns_id(store: TranscriptStore) -> None:
    tid = await store.log_transcript("hello", "focus")
    assert isinstance(tid, int)
    assert tid > 0


async def test_log_transcript_deduplication(store: TranscriptStore) -> None:
    """Logging the same transcript within 2 seconds returns existing ID."""
    import time

    text = "привіт світ"
    tid1 = await store.log_transcript(text, "ambient")
    # Sleep briefly to ensure different timestamp
    time.sleep(0.01)
    # Log same text again - should return existing ID, not create new row
    tid2 = await store.log_transcript(text, "ambient")
    assert tid1 == tid2, "Second log should return existing ID"

    # Verify only one row exists
    cursor = await store.db.execute(
        "SELECT COUNT(*) FROM transcripts WHERE text = ?", (text,)
    )
    row = await cursor.fetchone()
    assert row[0] == 1, "Should only have one transcript entry"


async def test_log_transcript_deduplication_after_window(store: TranscriptStore) -> None:
    """Logging same transcript after 2 seconds creates new row."""

    text = "тест повторення"
    tid1 = await store.log_transcript(text, "ambient")
    # Force time to pass the dedup window by manually inserting with old timestamp
    await store.db.execute(
        "UPDATE transcripts SET ts = ts - 3.0 WHERE id = ?", (tid1,)
    )
    await store.db.commit()
    # Log same text again - should create new row since old one is >2s ago
    tid2 = await store.log_transcript(text, "ambient")
    assert tid2 != tid1, "Should create new entry after dedup window"

    # Verify two rows exist
    cursor = await store.db.execute(
        "SELECT COUNT(*) FROM transcripts WHERE text = ?", (text,)
    )
    row = await cursor.fetchone()
    assert row[0] == 2, "Should have two transcript entries"


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


async def test_start_and_end_conversation(store: TranscriptStore) -> None:
    """Test starting and ending a conversation."""
    # Start a conversation
    conv_id = await store.start_conversation("ambient")
    assert conv_id > 0

    # Verify it's active
    active = await store.get_active_conversation()
    assert active is not None
    assert active["id"] == conv_id
    assert active["mode"] == "ambient"
    assert active["end_ts"] is None

    # End it
    await store.end_conversation(conv_id)

    # Verify it's no longer active
    active = await store.get_active_conversation()
    assert active is None


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


async def test_load_recent_action_log_orders_newest_first_and_caps_limit(
    store: TranscriptStore,
) -> None:
    import json

    base = time.time()
    for i in range(5):
        await store.upsert_action_log_entry(
            intent_id=100 + i,
            tool="web_search",
            args=f"query{i}",
            status="done",
            result=json.dumps({"summary": f"r{i}"}),
            ts=base + i,
        )
    rows = await store.load_recent_action_log(limit=3)
    assert len(rows) == 3
    assert rows[0]["intent_id"] == 104  # newest first
    assert rows[1]["intent_id"] == 103
    assert rows[2]["intent_id"] == 102


async def test_load_recent_action_log_filters_by_since_ts(
    store: TranscriptStore,
) -> None:
    import json

    now = time.time()
    # Older than threshold
    await store.upsert_action_log_entry(
        intent_id=200,
        tool="web_search",
        args="ancient",
        status="done",
        result=json.dumps({"summary": "old"}),
        ts=now - 3600,
    )
    # Right at the threshold (should be included via >=)
    await store.upsert_action_log_entry(
        intent_id=201,
        tool="web_search",
        args="threshold",
        status="done",
        result=json.dumps({"summary": "edge"}),
        ts=now - 1800,
    )
    # Within window
    await store.upsert_action_log_entry(
        intent_id=202,
        tool="web_search",
        args="recent",
        status="done",
        result=json.dumps({"summary": "fresh"}),
        ts=now - 60,
    )
    rows = await store.load_recent_action_log(since_ts=now - 1800)
    ids = {r["intent_id"] for r in rows}
    assert ids == {201, 202}
