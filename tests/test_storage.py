"""Tests for src/storage.py TranscriptStore against a temp SQLite db."""
from __future__ import annotations

import json
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


async def test_audio_event_columns_round_trip(store: TranscriptStore) -> None:
    await store.log_transcript(
        "hello there",
        "ambient",
        audio_event_label="Music",
        audio_event_score=0.82,
    )
    recent = await store.recent_transcripts(5)
    assert recent[0]["audio_event_label"] == "Music"
    assert recent[0]["audio_event_score"] == pytest.approx(0.82)


async def test_audio_event_columns_default_null(store: TranscriptStore) -> None:
    await store.log_transcript("plain turn", "ambient")
    recent = await store.recent_transcripts(5)
    assert recent[0]["audio_event_label"] is None
    assert recent[0]["audio_event_score"] is None


async def test_log_decision_and_action(store: TranscriptStore) -> None:
    tid = await store.log_transcript("запусти тести", "focus")
    decision = {
        "type": "act",
        "confidence": 0.9,
        "reason": "user asked",
        "intent": "run pytest",
        "action": {"tool": "Bash", "args": "pytest"},
    }
    did = await store.log_decision(tid, decision)
    assert did > 0
    aid = await store.log_action(did, "ok", "all tests passed")
    assert aid > 0


async def test_heartbeat_log(store: TranscriptStore) -> None:
    hid = await store.log_heartbeat(True, "як справи?")
    assert hid > 0
    hid2 = await store.log_heartbeat(False, None)
    assert hid2 > hid


async def test_recent_transcripts_ordering(store: TranscriptStore) -> None:
    await store.log_transcript("first", "ambient")
    await store.log_transcript("second", "ambient")
    await store.log_transcript("third", "ambient")
    recent = await store.recent_transcripts(2)
    assert [r["text"] for r in recent] == ["second", "third"]


async def test_purge_older_than_removes_old(store: TranscriptStore) -> None:
    tid = await store.log_transcript("old entry", "ambient")
    # backdate to 60 days ago via raw SQL
    old_ts = __import__("time").time() - 60 * 86400
    await store.db.execute("UPDATE transcripts SET ts = ? WHERE id = ?", (old_ts, tid))
    await store.db.commit()
    removed = await store.purge_older_than(30)
    assert removed >= 1
    recent = await store.recent_transcripts(10)
    assert all(r["id"] != tid for r in recent)


async def test_purge_older_than_keeps_recent(store: TranscriptStore) -> None:
    tid = await store.log_transcript("fresh entry", "ambient")
    removed = await store.purge_older_than(30)
    assert removed == 0
    recent = await store.recent_transcripts(10)
    assert any(r["id"] == tid for r in recent)


async def test_log_decision_with_none_fields(store: TranscriptStore) -> None:
    decision = {
        "type": "nothing",
        "confidence": None,
        "reply": None,
        "intent": None,
    }
    did = await store.log_decision(None, decision)
    assert did > 0


async def test_recent_transcripts_empty_db(store: TranscriptStore) -> None:
    recent = await store.recent_transcripts(5)
    assert recent == []


async def test_log_transcript_returns_id(store: TranscriptStore) -> None:
    tid = await store.log_transcript("hello", "focus")
    assert isinstance(tid, int)
    assert tid > 0


async def test_log_transcript_with_speaker_fields(store: TranscriptStore) -> None:
    tid = await store.log_transcript("привіт", "ambient", speaker_id="owner", speaker_confidence=0.92)
    assert tid > 0
    cursor = await store.db.execute(
        "SELECT speaker_id, speaker_confidence FROM transcripts WHERE id = ?",
        (tid,),
    )
    row = await cursor.fetchone()
    assert row == ("owner", 0.92)


async def test_log_transcript_without_speaker_fields_backward_compat(
    store: TranscriptStore,
) -> None:
    tid = await store.log_transcript("стара форма", "silent")
    cursor = await store.db.execute(
        "SELECT speaker_id, speaker_confidence FROM transcripts WHERE id = ?",
        (tid,),
    )
    row = await cursor.fetchone()
    assert row == (None, None)


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


async def test_migration_idempotent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "heare.db"
        s1 = TranscriptStore(db_path)
        await s1.init()
        tid = await s1.log_transcript("перше", "ambient", speaker_id="owner", speaker_confidence=0.9)
        await s1.close()

        s2 = TranscriptStore(db_path)
        await s2.init()  # must not crash with "duplicate column"
        recent = await s2.recent_transcripts(5)
        assert len(recent) == 1
        assert recent[0]["id"] == tid
        await s2.close()


# ---------------------------------------------------------------------------
# RT-001: schema v3 events table + EventKind + log_event + WAL + purge
# ---------------------------------------------------------------------------


async def test_schema_version_current_on_fresh_install(store: TranscriptStore) -> None:
    from src.store.storage import SCHEMA_VERSION

    cursor = await store.db.execute(
        "SELECT value FROM meta WHERE key = ?", ("schema_version",)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert int(row[0]) == SCHEMA_VERSION


async def test_schema_upgrade_v2_to_v3() -> None:
    import aiosqlite as _aiosqlite

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "v2.db"
        # Build a minimal v2 DB by hand: pre-populate meta and transcripts
        # without the events table.
        async with _aiosqlite.connect(db_path) as raw:
            await raw.executescript(
                """
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE transcripts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    text TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    speaker_id TEXT,
                    speaker_confidence REAL
                );
                CREATE TABLE decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    transcript_id INTEGER,
                    type TEXT NOT NULL,
                    confidence REAL,
                    reason TEXT,
                    reply TEXT,
                    intent TEXT,
                    action_json TEXT
                );
                CREATE TABLE actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    decision_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    result_summary TEXT
                );
                CREATE TABLE heartbeats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    decided_to_speak INTEGER NOT NULL,
                    reply TEXT
                );
                """
            )
            await raw.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)", ("schema_version", "2")
            )
            await raw.execute(
                "INSERT INTO transcripts (ts, text, mode) VALUES (?, ?, ?)",
                (time.time(), "pre-upgrade", "ambient"),
            )
            await raw.commit()

        # Now open via TranscriptStore — init() must upgrade in place
        s = TranscriptStore(db_path)
        await s.init()
        # meta.schema_version matches the current SCHEMA_VERSION constant
        from src.store.storage import SCHEMA_VERSION

        cursor = await s.db.execute(
            "SELECT value FROM meta WHERE key = ?", ("schema_version",)
        )
        row = await cursor.fetchone()
        assert row is not None
        assert int(row[0]) == SCHEMA_VERSION
        # events table exists
        cursor = await s.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
        )
        assert await cursor.fetchone() is not None
        # Pre-upgrade transcript preserved
        recent = await s.recent_transcripts(10)
        assert any(r["text"] == "pre-upgrade" for r in recent)
        await s.close()


async def test_log_event_happy_path(store: TranscriptStore) -> None:
    tid = await store.log_transcript("hello", "ambient")
    eid = await store.log_event(
        "decider.start", transcript_id=tid, payload={"mode": "ambient"}
    )
    assert eid > 0
    cursor = await store.db.execute(
        "SELECT kind, transcript_id, decision_id, payload_json FROM events WHERE id = ?",
        (eid,),
    )
    row = await cursor.fetchone()
    assert row is not None
    kind, got_tid, did, payload_json = row
    assert kind == "decider.start"
    assert got_tid == tid
    assert did is None
    assert json.loads(payload_json) == {"mode": "ambient"}


async def test_log_event_null_fks(store: TranscriptStore) -> None:
    eid = await store.log_event("state.listening")
    cursor = await store.db.execute(
        "SELECT transcript_id, decision_id, payload_json FROM events WHERE id = ?",
        (eid,),
    )
    row = await cursor.fetchone()
    assert row == (None, None, None)


async def test_log_event_accepts_strenum(store: TranscriptStore) -> None:
    from src.store.storage import EventKind

    eid = await store.log_event(EventKind.DECIDER_START)
    cursor = await store.db.execute(
        "SELECT kind FROM events WHERE id = ?", (eid,)
    )
    row = await cursor.fetchone()
    assert row == ("decider.start",)


async def test_purge_older_than_includes_events(store: TranscriptStore) -> None:
    eid = await store.log_event("decider.start", payload={"test": True})
    old_ts = time.time() - 60 * 86400
    await store.db.execute("UPDATE events SET ts = ? WHERE id = ?", (old_ts, eid))
    await store.db.commit()
    removed = await store.purge_older_than(30)
    assert removed >= 1
    cursor = await store.db.execute("SELECT COUNT(*) FROM events WHERE id = ?", (eid,))
    row = await cursor.fetchone()
    assert row == (0,)


async def test_wal_mode_set_after_init(store: TranscriptStore) -> None:
    cursor = await store.db.execute("PRAGMA journal_mode")
    row = await cursor.fetchone()
    assert row is not None
    assert row[0].lower() == "wal"


async def test_events_indexes_exist(store: TranscriptStore) -> None:
    cursor = await store.db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name IN ('idx_events_ts', 'idx_events_decision')"
    )
    rows = await cursor.fetchall()
    names = {r[0] for r in rows}
    assert names == {"idx_events_ts", "idx_events_decision"}


# ---------------------------------------------------------------------------
# DP-003: PRAGMA foreign_keys=ON + ON DELETE SET NULL cascade on events
# ---------------------------------------------------------------------------


async def test_foreign_keys_enabled_after_init(store: TranscriptStore) -> None:
    cursor = await store.db.execute("PRAGMA foreign_keys")
    row = await cursor.fetchone()
    assert row is not None
    assert int(row[0]) == 1


async def test_recent_transcripts_includes_speaker_id(store: TranscriptStore) -> None:
    await store.log_transcript("hello", "ambient", speaker_id="owner")
    await store.log_transcript("hi", "ambient", speaker_id="unknown")
    rows = await store.recent_transcripts(5)
    assert len(rows) == 2
    assert all("speaker_id" in r for r in rows)
    by_text = {r["text"]: r["speaker_id"] for r in rows}
    assert by_text["hello"] == "owner"
    assert by_text["hi"] == "unknown"


async def test_event_decision_id_cascade_sets_null(store: TranscriptStore) -> None:
    tid = await store.log_transcript("hello", "ambient")
    did = await store.log_decision(
        tid,
        {
            "type": "act",
            "confidence": 0.9,
            "reason": "asked",
            "reply": None,
            "intent": "run a thing",
            "action": {"tool": "Bash", "args": "echo hi"},
        },
    )
    eid = await store.log_event(
        "action.executing", decision_id=did, payload={"intent": "run a thing"}
    )

    await store.db.execute("DELETE FROM decisions WHERE id = ?", (did,))
    await store.db.commit()

    cursor = await store.db.execute(
        "SELECT decision_id FROM events WHERE id = ?", (eid,)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] is None


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


async def test_create_turn(store: TranscriptStore) -> None:
    """Test creating a turn in a conversation."""
    # Start a conversation
    conv_id = await store.start_conversation("focus")

    # Create a turn
    turn_id = await store.create_turn(
        conversation_id=conv_id,
        aggregated_text="Hello world, this is a test",
        utterance_count=3,
        topic_tags=["greeting", "test"],
    )
    assert turn_id > 0

    # Verify turn was created
    cursor = await store.db.execute(
        "SELECT aggregated_text, utterance_count, topic_tags FROM turns WHERE id = ?",
        (turn_id,),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "Hello world, this is a test"
    assert row[1] == 3
    assert row[2] == '["greeting", "test"]'


async def test_update_conversation_summary(store: TranscriptStore) -> None:
    """Test updating conversation summary."""
    conv_id = await store.start_conversation("ambient")

    # Update summary
    await store.update_conversation_summary(
        conversation_id=conv_id,
        summary="Discussed weather and plans",
        entity_map={"location": "Kyiv", "topic": "weather"},
    )

    # Verify summary was updated
    active = await store.get_active_conversation()
    assert active is not None
    assert active["summary"] == "Discussed weather and plans"
    assert active["entity_map"] == '{"location": "Kyiv", "topic": "weather"}'


async def test_get_recent_turns(store: TranscriptStore) -> None:
    """Test retrieving recent turns."""
    conv_id = await store.start_conversation("focus")

    # Create multiple turns
    await store.create_turn(conv_id, "First turn", 1, ["topic1"])
    await store.create_turn(conv_id, "Second turn", 2, ["topic2"])
    await store.create_turn(conv_id, "Third turn", 1, ["topic3"])

    # Get recent turns
    turns = await store.get_recent_turns(conv_id, n=2)
    assert len(turns) == 2
    assert turns[0]["aggregated_text"] == "Second turn"
    assert turns[1]["aggregated_text"] == "Third turn"


async def test_link_transcript_to_turn(store: TranscriptStore) -> None:
    """Test linking a transcript to a turn."""
    # Create a transcript
    tid = await store.log_transcript("test transcript", "focus")

    # Create a turn
    conv_id = await store.start_conversation("focus")
    turn_id = await store.create_turn(conv_id, "aggregated", 1)

    # Link them
    await store.link_transcript_to_turn(tid, turn_id)

    # Verify link
    cursor = await store.db.execute(
        "SELECT turn_id FROM transcripts WHERE id = ?", (tid,)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == turn_id


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


async def test_load_recent_action_log_excludes_legacy_decision_only_rows(
    store: TranscriptStore,
) -> None:
    # legacy log_action: decision-id only, intent_id IS NULL — must NOT
    # surface in the action_log projection.
    tid = await store.log_transcript("legacy", "ambient")
    did = await store.log_decision(tid, {"type": "act"})
    await store.log_action(did, "ok", "legacy summary")
    # action_log entry alongside it
    await store.upsert_action_log_entry(
        intent_id=300,
        tool="web_search",
        args="modern",
        status="done",
        result=json.dumps({"summary": "modern"}),
    )
    rows = await store.load_recent_action_log()
    ids = {r["intent_id"] for r in rows}
    assert ids == {300}


# ----------------------------------------------------------------------
# USE-001: usage_events ledger — paid API call tracking.


async def test_empty_usage_summary_returns_zero(store: TranscriptStore) -> None:
    summary = await store.get_usage_summary()
    assert summary["llm"] == {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    assert summary["stt"] == {"calls": 0, "audio_seconds": 0.0, "cost_usd": 0.0}
    assert summary["tts"] == {"calls": 0, "char_count": 0, "cost_usd": 0.0}
    assert summary["total_cost_usd"] == 0.0


async def test_record_and_summarize_llm_usage(store: TranscriptStore) -> None:
    await store.record_usage_event(
        kind="llm",
        provider="openrouter",
        model="google/gemini-3.1-flash-lite",
        input_tokens=10_000,
        output_tokens=5_000,
        cost_usd=0.00225,
    )
    summary = await store.get_usage_summary()
    assert summary["llm"]["calls"] == 1
    assert summary["llm"]["input_tokens"] == 10_000
    assert summary["llm"]["output_tokens"] == 5_000
    assert abs(summary["llm"]["cost_usd"] - 0.00225) < 1e-9
    assert abs(summary["total_cost_usd"] - 0.00225) < 1e-9


async def test_record_and_summarize_stt_usage(store: TranscriptStore) -> None:
    await store.record_usage_event(
        kind="stt",
        provider="groq",
        audio_seconds=120.0,
        cost_usd=0.00133,
    )
    summary = await store.get_usage_summary()
    assert summary["stt"]["calls"] == 1
    assert abs(summary["stt"]["audio_seconds"] - 120.0) < 1e-9
    assert abs(summary["stt"]["cost_usd"] - 0.00133) < 1e-9
    assert summary["llm"]["calls"] == 0
    assert summary["tts"]["calls"] == 0


async def test_record_and_summarize_tts_usage(store: TranscriptStore) -> None:
    await store.record_usage_event(
        kind="tts",
        provider="edge_tts",
        char_count=2_500,
        cost_usd=0.0,
    )
    summary = await store.get_usage_summary()
    assert summary["tts"]["calls"] == 1
    assert summary["tts"]["char_count"] == 2_500
    assert summary["tts"]["cost_usd"] == 0.0


async def test_summary_aggregates_multiple_events(store: TranscriptStore) -> None:
    """Two LLM calls + one STT → calls summed per kind, total spans all kinds."""
    await store.record_usage_event(
        kind="llm", provider="openrouter",
        model="google/gemini-3.1-flash-lite",
        input_tokens=10_000, output_tokens=5_000, cost_usd=0.00225,
    )
    await store.record_usage_event(
        kind="llm", provider="openrouter",
        model="google/gemini-3.1-flash-lite",
        input_tokens=2_000, output_tokens=1_000, cost_usd=0.00045,
    )
    await store.record_usage_event(
        kind="stt", provider="groq",
        audio_seconds=60.0, cost_usd=0.000667,
    )
    summary = await store.get_usage_summary()
    assert summary["llm"]["calls"] == 2
    assert summary["llm"]["input_tokens"] == 12_000
    assert summary["llm"]["output_tokens"] == 6_000
    assert abs(summary["llm"]["cost_usd"] - 0.0027) < 1e-9
    assert summary["stt"]["calls"] == 1
    expected_total = 0.00225 + 0.00045 + 0.000667
    assert abs(summary["total_cost_usd"] - expected_total) < 1e-9


async def test_summary_since_filters_old_events(store: TranscriptStore) -> None:
    """``since`` cutoff filters by ts — old events drop out of the window."""
    now = time.time()
    await store.record_usage_event(
        kind="llm", provider="openrouter",
        model="google/gemini-3.1-flash-lite",
        input_tokens=1_000, output_tokens=500, cost_usd=0.000225,
        ts=now - 7200,
    )
    await store.record_usage_event(
        kind="llm", provider="openrouter",
        model="google/gemini-3.1-flash-lite",
        input_tokens=2_000, output_tokens=1_000, cost_usd=0.00045,
        ts=now - 60,
    )
    summary = await store.get_usage_summary(since=now - 3600)
    assert summary["llm"]["calls"] == 1
    assert summary["llm"]["input_tokens"] == 2_000
    assert abs(summary["llm"]["cost_usd"] - 0.00045) < 1e-9


async def test_record_with_unknown_cost_stores_null(store: TranscriptStore) -> None:
    """Unknown model → cost_usd is None; row still recorded so call counts
    remain accurate. SUM(NULL) → COALESCE → 0.0 in the summary."""
    await store.record_usage_event(
        kind="llm", provider="openrouter",
        model="some/unknown-model",
        input_tokens=1_000, output_tokens=500, cost_usd=None,
    )
    summary = await store.get_usage_summary()
    assert summary["llm"]["calls"] == 1
    assert summary["llm"]["input_tokens"] == 1_000
    assert summary["llm"]["cost_usd"] == 0.0
    assert summary["total_cost_usd"] == 0.0
