"""Tests for src/storage.py TranscriptStore against a temp SQLite db."""
from __future__ import annotations

import tempfile
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
        with pytest.raises(RuntimeError, match=r"99.*2|2.*99"):
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
