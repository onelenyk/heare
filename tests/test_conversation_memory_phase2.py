"""Phase 2.2 US-P2.2-01 tests for ConversationManager action log."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.conversation import ConversationManager
from src.storage import TranscriptStore


@pytest.fixture
async def mgr():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "heare.db"
        store = TranscriptStore(db)
        await store.init()
        try:
            yield ConversationManager(store, MagicMock())
        finally:
            await store.close()


async def test_action_lifecycle_pending_to_done(mgr):
    mgr.record_action_pending(1, "bash", "echo hi")
    mgr.record_action_result(1, "ran: echo hi")
    entries = mgr.recent_actions()
    assert len(entries) == 1
    assert entries[0]["id"] == 1
    assert entries[0]["status"] == "done"
    assert entries[0]["result"] == "ran: echo hi"
    assert entries[0]["tool"] == "bash"
    assert entries[0]["args"] == "echo hi"


async def test_action_error_path(mgr):
    mgr.record_action_pending(7, "bash", "bad")
    mgr.record_action_error(7, "command not found")
    entries = mgr.recent_actions()
    assert len(entries) == 1
    assert entries[0]["status"] == "error"
    assert entries[0]["error"] == "command not found"


async def test_deque_cap_drops_oldest(mgr):
    # Maxlen is 16; push 20
    for i in range(1, 21):
        mgr.record_action_pending(i, "bash", f"cmd{i}")
    snapshot = list(mgr._action_log)
    assert len(snapshot) == 16
    # Oldest (ids 1-4) dropped; 5..20 remain
    assert snapshot[0]["id"] == 5
    assert snapshot[-1]["id"] == 20


async def test_recent_actions_returns_newest_first_limited(mgr):
    for i in range(1, 8):
        mgr.record_action_pending(i, "bash", f"c{i}")
    actions = mgr.recent_actions(limit=5)
    assert len(actions) == 5
    # Newest first
    assert [a["id"] for a in actions] == [7, 6, 5, 4, 3]


async def test_concurrent_record_and_snapshot_no_errors(mgr):
    """Single event loop; no lock needed. Atomic deque ops under GIL."""

    async def writer(start):
        for i in range(start, start + 50):
            mgr.record_action_pending(i, "bash", f"cmd{i}")
            await asyncio.sleep(0)  # yield to other coros

    async def reader():
        for _ in range(50):
            snap = mgr.recent_actions(limit=5)
            assert isinstance(snap, list)
            assert len(snap) <= 5
            await asyncio.sleep(0)

    await asyncio.gather(writer(0), writer(100), reader())
    # No exceptions raised — contract holds under interleaving


async def test_build_context_includes_recent_actions_when_inactive(mgr):
    mgr.record_action_pending(1, "bash", "echo hi")
    ctx = await mgr.build_context(conversation_id=None)
    assert "recent_actions" in ctx
    assert len(ctx["recent_actions"]) == 1
    assert ctx["recent_actions"][0]["id"] == 1
