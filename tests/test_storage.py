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
