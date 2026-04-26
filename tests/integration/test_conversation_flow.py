"""Integration tests for conversation flow with TurnAggregator.

Tests verify that TurnAggregator integrates correctly with the Pipecat pipeline
and that conversation memory works end-to-end.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import Mode, Settings
from src.conversation import ConversationManager
from src.storage import TranscriptStore
from src.turn_aggregator import TurnAggregator, TurnAggregatorProcessor


@pytest.fixture
def tmp_settings(tmp_path):
    """Create test settings with temporary directories."""
    s = Settings(
        workspace_dir=tmp_path / "workspace",
        session_file=tmp_path / "session.json",
        db_path=tmp_path / "test.db",
        log_dir=tmp_path / "logs",
        turn_aggregation_enabled=True,
        conversation_memory_enabled=True,
        topic_extraction_enabled=True,
        groq_api_key="test-key",
        mode=Mode.AMBIENT,
    )
    (tmp_path / "workspace").mkdir()
    (tmp_path / "logs").mkdir()
    return s


@pytest.mark.asyncio
async def test_turn_aggregator_with_conversation_manager(tmp_settings):
    """Test that TurnAggregator integrates correctly with ConversationManager.

    Verifies:
    - TurnAggregator processes utterances and detects turn completion
    - on_turn_complete callback invokes ConversationManager methods
    - Topics are extracted and conversation summary is updated
    """
    # Setup mocks
    mock_store = MagicMock(spec=TranscriptStore)
    mock_claude = AsyncMock()
    mock_claude.persona = None
    mock_claude.version = AsyncMock(return_value="1.0.0")

    # Create conversation manager
    conv_manager = ConversationManager(mock_store, mock_claude)

    # Mock conversation manager methods
    conv_manager.extract_topics = AsyncMock(return_value=["topic1", "topic2"])
    conv_manager.get_or_create_active = AsyncMock(return_value=1)
    conv_manager.update_summary = AsyncMock()

    # Track callback invocations
    callback_called = False
    callback_data = {}

    async def on_turn_complete(
        aggregated_text: str,
        turn_start_ts: float,
        turn_end_ts: float,
        buffer: list[dict],
    ) -> None:
        """Callback when a turn completes."""
        nonlocal callback_called, callback_data

        # Extract topics and update conversation
        topics = await conv_manager.extract_topics(aggregated_text)
        conv_id = await conv_manager.get_or_create_active()
        await conv_manager.update_summary(conv_id, aggregated_text, topics)

        callback_called = True
        callback_data = {
            "text": aggregated_text,
            "start_ts": turn_start_ts,
            "end_ts": turn_end_ts,
            "buffer": buffer,
            "topics": topics,
            "conv_id": conv_id,
        }

    # Create TurnAggregator with short timeout for testing
    aggregator = TurnAggregator(
        mode=Mode.AMBIENT,
        ambient_timeout=0.3,  # Short timeout for testing
        focus_timeout=0.1,
        on_turn_complete=on_turn_complete,
    )

    # Add utterances
    ts = time.time()
    should_submit, aggregated = await aggregator.add_utterance("First utterance", ts)
    assert not should_submit
    assert aggregated is None

    # Wait for timeout
    await asyncio.sleep(0.4)  # Longer than 0.3s timeout

    # Verify callback was invoked
    assert callback_called
    assert callback_data["text"] == "First utterance"
    assert callback_data["topics"] == ["topic1", "topic2"]
    assert callback_data["conv_id"] == 1

    # Verify conversation manager methods were called
    conv_manager.extract_topics.assert_called_once_with("First utterance")
    conv_manager.get_or_create_active.assert_called_once()
    conv_manager.update_summary.assert_called_once_with(1, "First utterance", ["topic1", "topic2"])


@pytest.mark.asyncio
async def test_turn_aggregator_processor_frame_processing():
    """Test that TurnAggregatorProcessor integrates with frame-based pipeline.

    Verifies:
    - TurnAggregatorProcessor can be created
    - It wraps TurnAggregator correctly
    - Settings are passed through correctly
    """
    # Create processor with various settings
    processor = TurnAggregatorProcessor(
        mode=Mode.FOCUS,
        focus_timeout=0.5,
        ambient_timeout=3.0,
        max_turn_duration=30.0,
        max_buffer_size=50,
    )

    # Verify aggregator was created with correct settings
    assert processor.aggregator.mode == Mode.FOCUS
    assert processor.aggregator.focus_timeout == 0.5
    assert processor.aggregator.ambient_timeout == 3.0
    assert processor.aggregator.max_turn_duration == 30.0
    assert processor.aggregator.max_buffer_size == 50

    # Verify mode can be changed
    processor.set_mode(Mode.AMBIENT)
    assert processor.aggregator.mode == Mode.AMBIENT


@pytest.mark.asyncio
async def test_turn_aggregation_mode_aware_timeouts():
    """Test that different modes use different timeouts.

    Verifies:
    - Focus mode uses shorter timeout (0.5s)
    - Ambient mode uses longer timeout (3.0s)
    """
    callback_invocations = []

    async def callback(text, start, end, buffer):
        callback_invocations.append({"text": text, "mode": "focus" if text.startswith("F") else "ambient"})

    # Focus mode aggregator
    focus_agg = TurnAggregator(
        mode=Mode.FOCUS,
        focus_timeout=0.2,  # Short for testing
        ambient_timeout=1.0,
        on_turn_complete=callback,
    )

    # Ambient mode aggregator
    ambient_agg = TurnAggregator(
        mode=Mode.AMBIENT,
        focus_timeout=0.2,
        ambient_timeout=1.0,  # Longer for testing
        on_turn_complete=callback,
    )

    import asyncio

    # Test Focus mode: should timeout quickly
    ts = time.time()
    await focus_agg.add_utterance("Focus test", ts)
    await asyncio.sleep(0.3)  # Wait for 0.2s timeout

    assert len(callback_invocations) == 1
    assert callback_invocations[0]["text"] == "Focus test"

    # Test Ambient mode: should wait longer
    ts = time.time()
    await ambient_agg.add_utterance("Ambient test", ts)
    await asyncio.sleep(0.1)  # NOT yet timed out
    await ambient_agg.add_utterance("still talking", ts + 0.05)  # Reset timeout
    await asyncio.sleep(1.1)  # Wait for 1.0s timeout

    assert len(callback_invocations) == 2
    assert callback_invocations[1]["text"] == "Ambient test still talking"


@pytest.mark.asyncio
async def test_conversation_manager_integration_flow(tmp_settings):
    """Test end-to-end conversation manager flow.

    Verifies:
    - Extract topics from aggregated text
    - Get or create active conversation
    - Update conversation summary with topics
    """
    # Setup mocks
    mock_store = MagicMock(spec=TranscriptStore)
    mock_claude = AsyncMock()
    mock_claude.persona = None
    mock_claude.call_decider = AsyncMock(
        return_value='["weather forecast", "calendar meeting", "code debugging"]'
    )

    # Create conversation manager
    conv_manager = ConversationManager(mock_store, mock_claude)

    # Simulate turn completion
    aggregated_text = "I need to check the weather forecast for tomorrow and then attend a calendar meeting about the new code debugging project"

    # Extract topics
    topics = await conv_manager.extract_topics(aggregated_text)
    assert isinstance(topics, list)
    assert len(topics) > 0

    # Get or create conversation
    mock_store.get_active_conversation = AsyncMock(return_value=None)
    mock_store.start_conversation = AsyncMock(return_value=42)
    conv_id = await conv_manager.get_or_create_active()
    assert conv_id == 42

    # Update summary - just verify it doesn't crash
    # (The actual update_conversation_summary is a no-op mock since we're testing the flow)
    try:
        await conv_manager.update_summary(conv_id, aggregated_text, topics)
        # If we get here without exception, the flow works
        assert True
    except Exception as e:
        pytest.fail(f"update_summary raised exception: {e}")


