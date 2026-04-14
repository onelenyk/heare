"""Tests for src/storage.py TranscriptStore against a temp SQLite db."""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import pytest

from src.storage import TranscriptStore


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


async def test_schema_version_fresh_install(store: TranscriptStore) -> None:
    cursor = await store.db.execute(
        "SELECT value FROM meta WHERE key = ?", ("schema_version",)
    )
    row = await cursor.fetchone()
    assert row is not None
    from src.storage import SCHEMA_VERSION

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


async def test_schema_version_v3_on_fresh_install(store: TranscriptStore) -> None:
    cursor = await store.db.execute(
        "SELECT value FROM meta WHERE key = ?", ("schema_version",)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert int(row[0]) == 3


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
        # meta.schema_version is now 3
        cursor = await s.db.execute(
            "SELECT value FROM meta WHERE key = ?", ("schema_version",)
        )
        row = await cursor.fetchone()
        assert row is not None
        assert int(row[0]) == 3
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
    from src.storage import EventKind

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
