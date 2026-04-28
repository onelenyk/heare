"""Tests for src/conversation.py ConversationManager with mocked Claude calls."""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.conversation import ConversationManager
from src.storage import TranscriptStore


class FakeClaudeCLI:
    """Fake Claude backend for testing."""

    def __init__(self, decision_response: dict[str, Any] | None = None) -> None:
        self._decision_response = decision_response or {"type": "nothing"}
        self.call_decider = AsyncMock(return_value=self._decision_response)
        self.call_action = AsyncMock(return_value={"summary": "ok"})
        self.bootstrap_identity = AsyncMock(return_value={})
        self.version = AsyncMock(return_value="1.0.0")
        self.persona = "test"

    async def __aenter__(self) -> "FakeClaudeCLI":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


@pytest.fixture
async def store():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "heare.db"
        s = TranscriptStore(db)
        await s.init()
        try:
            yield s
        finally:
            await s.close()


@pytest.fixture
def fake_claude():
    """Create a fake Claude backend."""
    return FakeClaudeCLI()


async def test_build_context_includes_all_fields(store: TranscriptStore, fake_claude: FakeClaudeCLI) -> None:
    """Test that build_context returns dict with all required keys."""
    conv_id = await store.start_conversation("ambient")

    manager = ConversationManager(store)
    ctx = await manager.build_context(conv_id)

    # Verify all required keys are present (Phase 2.2 adds recent_actions)
    assert set(ctx.keys()) == {
        "conversation_active",
        "conversation_summary",
        "active_topics",
        "entities",
        "recent_turns",
        "recent_transcripts",
        "recent_actions",
    }


async def test_build_context_with_no_conversation(store: TranscriptStore, fake_claude: FakeClaudeCLI) -> None:
    """Test that build_context handles None conversation_id."""
    manager = ConversationManager(store)
    ctx = await manager.build_context(None)

    assert ctx["conversation_active"] is False
    assert ctx["conversation_summary"] == ""
    assert ctx["active_topics"] == []
    assert ctx["entities"] == {}
    assert ctx["recent_turns"] == []
    assert ctx["recent_transcripts"] == ""


async def test_build_context_with_inactive_conversation(store: TranscriptStore, fake_claude: FakeClaudeCLI) -> None:
    """Test that build_context handles inactive conversation."""
    # Create and end a conversation
    conv_id = await store.start_conversation("ambient")
    await store.end_conversation(conv_id)

    manager = ConversationManager(store)
    ctx = await manager.build_context(conv_id)

    assert ctx["conversation_active"] is False
    assert ctx["conversation_summary"] == ""


async def test_active_topics_from_last_2_turns(store: TranscriptStore, fake_claude: FakeClaudeCLI) -> None:
    """Test that active_topics are extracted from last 2 turns."""
    conv_id = await store.start_conversation("focus")

    # Create 3 turns with different topics
    await store.create_turn(conv_id, "Turn 1", 1, ["topic1", "topic2"])
    await store.create_turn(conv_id, "Turn 2", 1, ["topic3", "topic4"])
    await store.create_turn(conv_id, "Turn 3", 1, ["topic5", "topic6"])

    manager = ConversationManager(store)
    ctx = await manager.build_context(conv_id)

    # Should have topics from last 2 turns (turns 2 and 3)
    assert ctx["conversation_active"] is True
    assert "topic3" in ctx["active_topics"]
    assert "topic5" in ctx["active_topics"]
    # topic1 should NOT be in active topics (from turn 1, not last 2)
    assert "topic1" not in ctx["active_topics"]


async def test_active_topics_deduplicates(store: TranscriptStore, fake_claude: FakeClaudeCLI) -> None:
    """Test that active_topics deduplicates across turns."""
    conv_id = await store.start_conversation("ambient")

    # Create turns with overlapping topics
    await store.create_turn(conv_id, "Turn 1", 1, ["weather", "meeting"])
    await store.create_turn(conv_id, "Turn 2", 1, ["weather", "coding"])

    manager = ConversationManager(store)
    ctx = await manager.build_context(conv_id)

    # Weather should appear only once
    assert ctx["active_topics"].count("weather") == 1
    assert set(ctx["active_topics"]) == {"weather", "meeting", "coding"}


async def test_recent_turns_verbatim(store: TranscriptStore, fake_claude: FakeClaudeCLI) -> None:
    """Test that recent_turns returns last 3 turns with all fields."""
    conv_id = await store.start_conversation("focus")

    # Create 5 turns
    for i in range(5):
        await store.create_turn(
            conversation_id=conv_id,
            aggregated_text=f"Turn {i+1}",
            utterance_count=i + 1,
            topic_tags=[f"topic{i}"],
        )

    manager = ConversationManager(store)
    ctx = await manager.build_context(conv_id)

    # Should have last 3 turns
    assert len(ctx["recent_turns"]) == 3
    # Verify they're the last 3 (turns 3, 4, 5)
    assert ctx["recent_turns"][0]["aggregated_text"] == "Turn 3"
    assert ctx["recent_turns"][1]["aggregated_text"] == "Turn 4"
    assert ctx["recent_turns"][2]["aggregated_text"] == "Turn 5"

    # Verify all required fields are present
    for turn in ctx["recent_turns"]:
        assert "id" in turn
        assert "start_ts" in turn
        assert "end_ts" in turn
        assert "aggregated_text" in turn
        assert "utterance_count" in turn
        assert "topic_tags" in turn


async def test_recent_transcripts_fallback(store: TranscriptStore, fake_claude: FakeClaudeCLI) -> None:
    """Test that recent_transcripts provides fallback string."""
    conv_id = await store.start_conversation("ambient")

    # Create turns
    await store.create_turn(conv_id, "First turn", 1, ["topic1"])
    await store.create_turn(conv_id, "Second turn", 1, ["topic2"])

    manager = ConversationManager(store)
    ctx = await manager.build_context(conv_id)

    # Verify recent_transcripts is a joined string
    assert isinstance(ctx["recent_transcripts"], str)
    assert "First turn" in ctx["recent_transcripts"]
    assert "Second turn" in ctx["recent_transcripts"]


async def test_get_or_create_active_returns_existing(store: TranscriptStore, fake_claude: FakeClaudeCLI) -> None:
    """Test that get_or_create_active returns existing active conversation."""
    # Create an active conversation
    existing_id = await store.start_conversation("ambient")

    manager = ConversationManager(store)
    result_id = await manager.get_or_create_active()

    # Should return existing conversation
    assert result_id == existing_id


async def test_get_or_create_active_creates_new(store: TranscriptStore, fake_claude: FakeClaudeCLI) -> None:
    """Test that get_or_create_active creates new conversation when none exists."""
    # No active conversation
    manager = ConversationManager(store)
    result_id = await manager.get_or_create_active()

    # Should create new conversation
    assert result_id > 0

    # Verify it's active
    active = await store.get_active_conversation()
    assert active is not None
    assert active["id"] == result_id


async def test_get_or_create_active_ends_old_conversation(store: TranscriptStore, fake_claude: FakeClaudeCLI) -> None:
    """Test that get_or_create_active ends conversations older than 30 minutes."""
    # Create an old conversation
    old_id = await store.start_conversation("ambient")

    # Backdate it to 35 minutes ago
    import time

    old_ts = time.time() - (35 * 60)
    await store.db.execute(
        "UPDATE conversations SET start_ts = ? WHERE id = ?", (old_ts, old_id)
    )
    await store.db.commit()

    manager = ConversationManager(store)
    result_id = await manager.get_or_create_active()

    # Should create new conversation (old one ended)
    assert result_id != old_id

    # Verify old conversation is ended
    cursor = await store.db.execute(
        "SELECT end_ts FROM conversations WHERE id = ?", (old_id,)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] is not None  # end_ts should be set


async def test_get_or_create_active_within_30_minutes(store: TranscriptStore, fake_claude: FakeClaudeCLI) -> None:
    """Test that get_or_create_active reuses conversations younger than 30 minutes."""
    # Create a recent conversation
    recent_id = await store.start_conversation("ambient")

    # Backdate it to 20 minutes ago (within 30 min window)
    import time

    recent_ts = time.time() - (20 * 60)
    await store.db.execute(
        "UPDATE conversations SET start_ts = ? WHERE id = ?", (recent_ts, recent_id)
    )
    await store.db.commit()

    manager = ConversationManager(store)
    result_id = await manager.get_or_create_active()

    # Should reuse existing conversation
    assert result_id == recent_id

    # Verify it's still active (not ended)
    cursor = await store.db.execute(
        "SELECT end_ts FROM conversations WHERE id = ?", (recent_id,)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] is None  # end_ts should still be NULL


# ============================================================================
# CCS-01: action_log persistence
# ============================================================================


class TestActionLogPersistence:
    """Tests for the SQLite-backed action_log projection (CCS-01)."""

    async def _wait_persist(self) -> None:
        # The persist task is fire-and-forget. Yield to let it run; one
        # extra sleep(0) is sufficient because the queue is unbounded.
        import asyncio as _aio

        for _ in range(5):
            await _aio.sleep(0)

    async def test_record_action_pending_writes_to_db(
        self, store: TranscriptStore, fake_claude: FakeClaudeCLI
    ) -> None:
        manager = ConversationManager(store)
        manager.record_action_pending(7, "web_search", "chili recipe")
        await self._wait_persist()
        cursor = await store.db.execute(
            "SELECT status, tool, args FROM actions WHERE intent_id = ?", (7,)
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "pending"
        assert row[1] == "web_search"
        assert row[2] == "chili recipe"

    async def test_record_action_result_upserts_existing_pending(
        self, store: TranscriptStore, fake_claude: FakeClaudeCLI
    ) -> None:
        manager = ConversationManager(store)
        manager.record_action_pending(8, "web_search", "chili recipe")
        await self._wait_persist()
        manager.record_action_result(8, "found 5 hits")
        await self._wait_persist()
        cursor = await store.db.execute(
            "SELECT id, status, tool, result_json FROM actions WHERE intent_id = ?",
            (8,),
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1, "UPSERT must keep a single row per intent"
        assert rows[0][1] == "done"
        assert rows[0][2] == "web_search"
        assert "summary" in rows[0][3]
        assert "found 5 hits" in rows[0][3]

    async def test_record_action_error_upserts_existing_pending(
        self, store: TranscriptStore, fake_claude: FakeClaudeCLI
    ) -> None:
        manager = ConversationManager(store)
        manager.record_action_pending(9, "bash", "rm -rf /")
        await self._wait_persist()
        manager.record_action_error(9, "rejected: dangerous")
        await self._wait_persist()
        cursor = await store.db.execute(
            "SELECT status, result_json FROM actions WHERE intent_id = ?", (9,)
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "error"
        assert "error" in rows[0][1]
        assert "rejected: dangerous" in rows[0][1]

    async def test_persist_failure_does_not_raise(
        self, store: TranscriptStore, fake_claude: FakeClaudeCLI
    ) -> None:
        from unittest.mock import AsyncMock as _AsyncMock

        manager = ConversationManager(store)
        # Patch the store method to raise — the in-memory deque must still
        # update and no exception bubbles to the caller.
        store.upsert_action_log_entry = _AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("DB locked")
        )
        manager.record_action_pending(10, "web_search", "x")
        # In-memory deque still has the entry
        assert any(e["id"] == 10 for e in manager._action_log)
        # Wait for the fire-and-forget task to fail silently
        await self._wait_persist()
        # No exception bubbled

    async def test_hydrate_action_log_restores_recent_entries(
        self, store: TranscriptStore, fake_claude: FakeClaudeCLI
    ) -> None:
        # Write entries via the first manager
        m1 = ConversationManager(store)
        m1.record_action_pending(1, "web_search", "alpha")
        m1.record_action_result(1, "alpha summary")
        m1.record_action_pending(2, "web_search", "beta")
        m1.record_action_result(2, "beta summary")
        await self._wait_persist()
        # Simulate restart: brand-new manager hydrates from disk.
        m2 = ConversationManager(store)
        await m2.hydrate_action_log()
        recent = m2.recent_actions(limit=5)
        ids = {e["id"] for e in recent}
        assert {1, 2}.issubset(ids)
        # Tool and result preserved
        beta = next(e for e in recent if e["id"] == 2)
        assert beta["tool"] == "web_search"
        assert beta["result"] == "beta summary"
        assert beta["status"] == "done"

    async def test_record_action_result_with_items_persists_and_hydrates(
        self, store: TranscriptStore, fake_claude: FakeClaudeCLI
    ) -> None:
        """CCS-02: structured items round-trip via persistence/hydrate."""
        m1 = ConversationManager(store)
        items = [
            {"n": 1, "title": "T1", "url": "https://e.com/1", "snippet": "S1"},
            {"n": 2, "title": "T2", "url": "https://e.com/2", "snippet": "S2"},
        ]
        m1.record_action_pending(11, "web_search", "chili recipe")
        m1.record_action_result(11, "two hits", items=items)
        # In-memory entry has items
        live = next(e for e in m1.recent_actions() if e["id"] == 11)
        assert live["items"] == items
        await self._wait_persist()
        # Verify result_json contains items + summary
        cursor = await store.db.execute(
            "SELECT result_json FROM actions WHERE intent_id = ?", (11,)
        )
        row = await cursor.fetchone()
        assert row is not None
        import json as _json
        parsed = _json.loads(row[0])
        assert parsed.get("summary") == "two hits"
        assert parsed.get("items") == items

        # Restart hydrate: items survive
        m2 = ConversationManager(store)
        await m2.hydrate_action_log()
        restored = next(e for e in m2.recent_actions(limit=5) if e["id"] == 11)
        assert restored["items"] == items
        assert restored["result"] == "two hits"

    async def test_record_action_result_without_items_backward_compatible(
        self, store: TranscriptStore, fake_claude: FakeClaudeCLI
    ) -> None:
        """CCS-02: omitting items keeps the legacy result_json shape
        ({\"summary\": ...}) — no `items` key persisted."""
        m1 = ConversationManager(store)
        m1.record_action_pending(12, "bash", "echo hi")
        m1.record_action_result(12, "ran: hi")
        await self._wait_persist()
        cursor = await store.db.execute(
            "SELECT result_json FROM actions WHERE intent_id = ?", (12,)
        )
        row = await cursor.fetchone()
        import json as _json
        parsed = _json.loads(row[0])
        assert parsed == {"summary": "ran: hi"}

    async def test_hydrate_action_log_filters_by_since_ts(
        self, store: TranscriptStore, fake_claude: FakeClaudeCLI
    ) -> None:
        import json as _json

        now = time.time()
        # 6 hours ago — should be filtered out
        await store.upsert_action_log_entry(
            intent_id=20,
            tool="web_search",
            args="ancient",
            status="done",
            result=_json.dumps({"summary": "old"}),
            ts=now - 21600,
        )
        # 5 minutes ago — should be hydrated
        await store.upsert_action_log_entry(
            intent_id=21,
            tool="web_search",
            args="recent",
            status="done",
            result=_json.dumps({"summary": "fresh"}),
            ts=now - 300,
        )
        manager = ConversationManager(store)
        await manager.hydrate_action_log(since_ts=now - 1800)  # 30-min window
        ids = {e["id"] for e in manager.recent_actions(limit=10)}
        assert ids == {21}

    async def test_hydrate_action_log_no_since_ts_loads_all(
        self, store: TranscriptStore, fake_claude: FakeClaudeCLI
    ) -> None:
        import json as _json

        now = time.time()
        await store.upsert_action_log_entry(
            intent_id=30,
            tool="web_search",
            args="ancient",
            status="done",
            result=_json.dumps({"summary": "old"}),
            ts=now - 21600,
        )
        await store.upsert_action_log_entry(
            intent_id=31,
            tool="web_search",
            args="recent",
            status="done",
            result=_json.dumps({"summary": "fresh"}),
            ts=now - 300,
        )
        manager = ConversationManager(store)
        await manager.hydrate_action_log()  # no since_ts → both
        ids = {e["id"] for e in manager.recent_actions(limit=10)}
        assert ids == {30, 31}

    async def test_hydrate_preserves_chronological_order(
        self, store: TranscriptStore, fake_claude: FakeClaudeCLI
    ) -> None:
        import json as _json

        base = time.time()
        for i in range(3):
            await store.upsert_action_log_entry(
                intent_id=40 + i,
                tool="web_search",
                args=f"q{i}",
                status="done",
                result=_json.dumps({"summary": f"r{i}"}),
                ts=base + i,
            )
        manager = ConversationManager(store)
        await manager.hydrate_action_log()
        # recent_actions returns newest-first
        recent = manager.recent_actions(limit=5)
        assert [e["id"] for e in recent] == [42, 41, 40]
