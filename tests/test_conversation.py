"""Tests for src/conversation.py ConversationManager with mocked Claude calls."""
from __future__ import annotations

import json
import tempfile
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


async def test_extract_topics_returns_list(store: TranscriptStore, fake_claude: FakeClaudeCLI) -> None:
    """Test that extract_topics returns a list of topic strings."""
    # Mock Claude to return JSON array
    fake_claude.call_decider = AsyncMock(
        return_value='["weather forecast", "calendar meeting", "code debugging"]'
    )

    manager = ConversationManager(store, fake_claude)
    topics = await manager.extract_topics("Let's discuss the weather and our meeting")

    assert isinstance(topics, list)
    assert len(topics) == 3
    assert topics == ["weather forecast", "calendar meeting", "code debugging"]
    assert fake_claude.call_decider.await_count == 1


async def test_extract_topics_handles_dict_response(store: TranscriptStore, fake_claude: FakeClaudeCLI) -> None:
    """Test that extract_topics handles dict response with 'result' field."""
    # Mock Claude to return dict with result field (SDK format)
    fake_claude.call_decider = AsyncMock(
        return_value={"result": '["topic1", "topic2", "topic3"]'}
    )

    manager = ConversationManager(store, fake_claude)
    topics = await manager.extract_topics("Test text")

    assert isinstance(topics, list)
    assert len(topics) == 3
    assert topics == ["topic1", "topic2", "topic3"]


async def test_extract_topics_handles_markdown_fences(store: TranscriptStore, fake_claude: FakeClaudeCLI) -> None:
    """Test that extract_topics strips markdown fences."""
    # Mock Claude to return JSON in markdown fences
    fake_claude.call_decider = AsyncMock(
        return_value='```json\n["AI development", "testing", "code review"]\n```'
    )

    manager = ConversationManager(store, fake_claude)
    topics = await manager.extract_topics("Let's talk about AI and testing")

    assert isinstance(topics, list)
    assert len(topics) == 3
    assert "AI development" in topics


async def test_extract_topics_limits_to_5(store: TranscriptStore, fake_claude: FakeClaudeCLI) -> None:
    """Test that extract_topics limits results to 5 topics."""
    # Mock Claude to return more than 5 topics
    fake_claude.call_decider = AsyncMock(
        return_value='["t1", "t2", "t3", "t4", "t5", "t6", "t7"]'
    )

    manager = ConversationManager(store, fake_claude)
    topics = await manager.extract_topics("Many topics here")

    assert isinstance(topics, list)
    assert len(topics) == 5  # Should limit to 5


async def test_extract_topics_handles_parse_error(store: TranscriptStore, fake_claude: FakeClaudeCLI) -> None:
    """Test that extract_topics returns empty list on parse error."""
    # Mock Claude to return invalid JSON
    fake_claude.call_decider = AsyncMock(return_value="not valid json")

    manager = ConversationManager(store, fake_claude)
    topics = await manager.extract_topics("Test text")

    assert isinstance(topics, list)
    assert len(topics) == 0


class FakeTopicExtractor:
    """Minimal fake matching the OpenRouterTopicExtractorCLI interface."""

    def __init__(self, return_value: list[str] | None = None) -> None:
        self._return_value = return_value or []
        self.extract_topics = AsyncMock(return_value=self._return_value)


async def test_extract_topics_uses_extractor_when_injected(
    store: TranscriptStore, fake_claude: FakeClaudeCLI
) -> None:
    """When topic_extractor is provided, the OpenRouter path runs and call_decider is NOT invoked."""
    extractor = FakeTopicExtractor(return_value=["alpha", "beta", "gamma"])
    manager = ConversationManager(store, fake_claude, topic_extractor=extractor)

    topics = await manager.extract_topics("some text")

    assert topics == ["alpha", "beta", "gamma"]
    assert extractor.extract_topics.await_count == 1
    assert fake_claude.call_decider.await_count == 0


async def test_extract_topics_uses_claude_when_no_extractor(
    store: TranscriptStore, fake_claude: FakeClaudeCLI
) -> None:
    """When topic_extractor is None (default), the Claude path runs."""
    fake_claude.call_decider = AsyncMock(return_value='["x","y"]')
    manager = ConversationManager(store, fake_claude)  # no topic_extractor

    topics = await manager.extract_topics("some text")

    assert topics == ["x", "y"]
    assert fake_claude.call_decider.await_count == 1


async def test_extract_topics_emits_timing_log_openrouter(
    store: TranscriptStore, fake_claude: FakeClaudeCLI, caplog: pytest.LogCaptureFixture
) -> None:
    """OpenRouter path emits a TOPIC EXTRACT backend=openrouter log line."""
    import logging

    extractor = FakeTopicExtractor(return_value=["a", "b"])
    manager = ConversationManager(store, fake_claude, topic_extractor=extractor)
    with caplog.at_level(logging.INFO, logger="heare.conversation"):
        await manager.extract_topics("text")
    assert any(
        "TOPIC EXTRACT backend=openrouter" in rec.getMessage()
        for rec in caplog.records
    )


async def test_extract_topics_emits_timing_log_claude(
    store: TranscriptStore, fake_claude: FakeClaudeCLI, caplog: pytest.LogCaptureFixture
) -> None:
    """Claude path emits a TOPIC EXTRACT backend=claude log line."""
    import logging

    fake_claude.call_decider = AsyncMock(return_value='["a"]')
    manager = ConversationManager(store, fake_claude)
    with caplog.at_level(logging.INFO, logger="heare.conversation"):
        await manager.extract_topics("text")
    assert any(
        "TOPIC EXTRACT backend=claude" in rec.getMessage()
        for rec in caplog.records
    )


async def test_update_summary_merges_topics(store: TranscriptStore, fake_claude: FakeClaudeCLI) -> None:
    """Test that update_summary merges topics into conversation."""
    # Create a conversation
    conv_id = await store.start_conversation("ambient")

    manager = ConversationManager(store, fake_claude)
    await manager.update_summary(
        conversation_id=conv_id,
        turn_text="Let's discuss the project timeline",
        topics=["project management", "timeline"],
    )

    # Verify summary was updated
    conv = await store.get_active_conversation()
    assert conv is not None
    assert conv["id"] == conv_id
    assert "project timeline" in conv["summary"]

    # Verify entity map includes topics
    assert conv["entity_map"] is not None
    entity_map = json.loads(conv["entity_map"])
    assert "topics" in entity_map
    assert entity_map["topics"] == ["project management", "timeline"]


async def test_update_summary_keeps_recent_verbatim(store: TranscriptStore, fake_claude: FakeClaudeCLI) -> None:
    """Test that update_summary keeps last 3 turns verbatim."""
    conv_id = await store.start_conversation("focus")

    manager = ConversationManager(store, fake_claude)

    # Add 4 turns
    for i in range(4):
        await store.create_turn(
            conversation_id=conv_id,
            aggregated_text=f"Turn {i+1} text",
            utterance_count=1,
            topic_tags=[f"topic{i}"],
        )
        await manager.update_summary(
            conversation_id=conv_id,
            turn_text=f"Turn {i+1} text",
            topics=[f"topic{i}"],
        )

    # Verify summary keeps last 3 verbatim
    conv = await store.get_active_conversation()
    assert conv is not None
    # Should have last 3 turns verbatim in summary
    assert "Turn 2 text" in conv["summary"] or "Turn 3 text" in conv["summary"] or "Turn 4 text" in conv["summary"]


async def test_update_summary_extracts_entities(store: TranscriptStore, fake_claude: FakeClaudeCLI) -> None:
    """Test that update_summary extracts capitalized entities."""
    conv_id = await store.start_conversation("ambient")

    manager = ConversationManager(store, fake_claude)
    await manager.update_summary(
        conversation_id=conv_id,
        turn_text="Let's meet with John Smith in Kyiv tomorrow",
        topics=["meeting"],
    )

    # Verify entity map includes mentioned entities
    conv = await store.get_active_conversation()
    assert conv is not None
    entity_map = json.loads(conv["entity_map"])
    assert "mentioned" in entity_map
    # Should capture some capitalized words
    assert len(entity_map["mentioned"]) > 0


async def test_build_context_includes_all_fields(store: TranscriptStore, fake_claude: FakeClaudeCLI) -> None:
    """Test that build_context returns dict with all required keys."""
    conv_id = await store.start_conversation("ambient")

    manager = ConversationManager(store, fake_claude)
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
    manager = ConversationManager(store, fake_claude)
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

    manager = ConversationManager(store, fake_claude)
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

    manager = ConversationManager(store, fake_claude)
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

    manager = ConversationManager(store, fake_claude)
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

    manager = ConversationManager(store, fake_claude)
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

    manager = ConversationManager(store, fake_claude)
    ctx = await manager.build_context(conv_id)

    # Verify recent_transcripts is a joined string
    assert isinstance(ctx["recent_transcripts"], str)
    assert "First turn" in ctx["recent_transcripts"]
    assert "Second turn" in ctx["recent_transcripts"]


async def test_get_or_create_active_returns_existing(store: TranscriptStore, fake_claude: FakeClaudeCLI) -> None:
    """Test that get_or_create_active returns existing active conversation."""
    # Create an active conversation
    existing_id = await store.start_conversation("ambient")

    manager = ConversationManager(store, fake_claude)
    result_id = await manager.get_or_create_active()

    # Should return existing conversation
    assert result_id == existing_id


async def test_get_or_create_active_creates_new(store: TranscriptStore, fake_claude: FakeClaudeCLI) -> None:
    """Test that get_or_create_active creates new conversation when none exists."""
    # No active conversation
    manager = ConversationManager(store, fake_claude)
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

    manager = ConversationManager(store, fake_claude)
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

    manager = ConversationManager(store, fake_claude)
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
