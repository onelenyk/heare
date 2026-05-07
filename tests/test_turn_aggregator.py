"""Tests for TurnAggregator frame processor."""
from __future__ import annotations

import asyncio
import time

import pytest

from src.config import Mode
from src.pipeline.stages.turn_aggregator import TurnAggregator


@pytest.fixture
def focus_aggregator():
    """Create a TurnAggregator in FOCUS mode (0.5s timeout)."""
    return TurnAggregator(mode=Mode.FOCUS)


@pytest.fixture
def ambient_aggregator():
    """Create a TurnAggregator in AMBIENT mode (3.0s timeout)."""
    return TurnAggregator(mode=Mode.AMBIENT)


@pytest.mark.asyncio
async def test_focus_mode_quick_reply(focus_aggregator):
    """Focus mode submits after 0.5s silence."""
    ts = time.time()

    # Add first utterance
    should_submit, aggregated = await focus_aggregator.add_utterance("Hello", ts)
    assert not should_submit
    assert aggregated is None
    assert len(focus_aggregator.buffer) == 1

    # Wait 0.6s (longer than 0.5s timeout)
    await asyncio.sleep(0.6)

    # Timeout should have triggered, but we can't check the callback result
    # The buffer should be cleared by timeout handler
    assert len(focus_aggregator.buffer) == 0


@pytest.mark.asyncio
async def test_ambient_mode_aggregation(ambient_aggregator):
    """Ambient mode waits 3.0s before submitting, allowing multiple utterances."""
    ts = time.time()

    # Add first utterance
    should_submit, aggregated = await ambient_aggregator.add_utterance("I'm thinking", ts)
    assert not should_submit
    assert len(ambient_aggregator.buffer) == 1

    # Add second utterance after 0.5s (within timeout)
    await asyncio.sleep(0.5)
    should_submit, aggregated = await ambient_aggregator.add_utterance("about the project", ts + 0.5)
    assert not should_submit
    assert len(ambient_aggregator.buffer) == 2

    # Add third utterance after another 0.5s
    await asyncio.sleep(0.5)
    should_submit, aggregated = await ambient_aggregator.add_utterance("and we should focus on X", ts + 1.0)
    assert not should_submit
    assert len(ambient_aggregator.buffer) == 3

    # Wait 3.1s (longer than 3.0s timeout)
    await asyncio.sleep(3.1)

    # Timeout should have triggered and cleared buffer
    assert len(ambient_aggregator.buffer) == 0


@pytest.mark.asyncio
async def test_max_turn_duration_forces_submit():
    """Max turn duration (30s) forces submit even with active speech."""
    aggregator = TurnAggregator(mode=Mode.FOCUS, max_turn_duration=30.0)
    ts = time.time()

    # Add utterances over time, staying under 30s
    for i in range(5):
        should_submit, aggregated = await aggregator.add_utterance(f"Utterance {i}", ts + i * 5)
        assert not should_submit, f"Should not submit before 30s mark (utterance {i})"

    # Add utterance at 31s - should force submit
    should_submit, aggregated = await aggregator.add_utterance("Final utterance", ts + 31)
    assert should_submit, "Should force submit after 30s max duration"
    assert aggregated is not None
    assert "Utterance" in aggregated
    assert "Final utterance" in aggregated


@pytest.mark.asyncio
async def test_max_buffer_size_forces_submit():
    """Max buffer size (50 utterances) forces submit."""
    aggregator = TurnAggregator(mode=Mode.FOCUS, max_buffer_size=50)
    ts = time.time()

    # Add 49 utterances - should not submit
    for i in range(49):
        should_submit, aggregated = await aggregator.add_utterance(f"Word {i}", ts)
        assert not should_submit, f"Should not submit before buffer limit (utterance {i})"
        assert len(aggregator.buffer) == i + 1

    # Add 50th utterance - should force submit
    should_submit, aggregated = await aggregator.add_utterance("Word 49", ts)
    assert should_submit, "Should force submit at buffer limit (50)"
    assert aggregated is not None
    assert "Word 48" in aggregated  # Previous utterances included
    assert "Word 49" in aggregated  # Current utterance included


@pytest.mark.asyncio
async def test_mode_change_clears_buffer():
    """Changing mode clears the buffer and resets state."""
    aggregator = TurnAggregator(mode=Mode.FOCUS)
    ts = time.time()

    # Add some utterances in FOCUS mode
    await aggregator.add_utterance("First", ts)
    await aggregator.add_utterance("Second", ts + 0.1)
    await aggregator.add_utterance("Third", ts + 0.2)

    assert len(aggregator.buffer) == 3
    assert aggregator.turn_start_ts is not None

    # Switch to AMBIENT mode - should clear buffer
    aggregator.set_mode(Mode.AMBIENT)

    assert len(aggregator.buffer) == 0, "Buffer should be cleared on mode change"
    assert aggregator.turn_start_ts is None, "Turn start should be reset on mode change"
    assert aggregator.mode == Mode.AMBIENT

    # Switch to SILENT mode - should also clear
    await aggregator.add_utterance("Should be cleared", ts + 0.3)
    assert len(aggregator.buffer) == 1

    aggregator.set_mode(Mode.SILENT)
    assert len(aggregator.buffer) == 0, "Buffer should be cleared when switching to SILENT"


@pytest.mark.asyncio
async def test_aggregate_and_clear():
    """Test the internal _aggregate_and_clear method."""
    aggregator = TurnAggregator(mode=Mode.FOCUS)
    ts = time.time()

    # Manually populate buffer (bypassing add_utterance)
    aggregator.buffer = [
        {"text": "Hello", "utterance_ts": ts},
        {"text": "world", "utterance_ts": ts + 0.1},
        {"text": "how", "utterance_ts": ts + 0.2},
        {"text": "are you", "utterance_ts": ts + 0.3},
    ]
    aggregator.turn_start_ts = ts
    aggregator.last_utterance_ts = ts + 0.3

    # Aggregate and clear
    result = aggregator._aggregate_and_clear()

    assert result == "Hello world how are you"
    assert len(aggregator.buffer) == 0
    assert aggregator.turn_start_ts is None
    assert aggregator.last_utterance_ts is None


@pytest.mark.asyncio
async def test_timeout_cancellation_on_new_utterance():
    """New utterances cancel the previous timeout task."""
    aggregator = TurnAggregator(mode=Mode.FOCUS)
    ts = time.time()

    # Add first utterance
    should_submit, _ = await aggregator.add_utterance("First", ts)
    assert not should_submit

    # Store reference to timeout task
    first_task = aggregator._timeout_task
    assert first_task is not None
    assert not first_task.done()

    # Add second utterance after 0.2s (before timeout)
    await asyncio.sleep(0.2)
    should_submit, _ = await aggregator.add_utterance("Second", ts + 0.2)
    assert not should_submit

    # New task should be created (first task is cancelled or cancelling)
    second_task = aggregator._timeout_task
    assert second_task is not None
    assert second_task != first_task
    assert not second_task.done()

    # First task should be in cancelled state (or cancelling)
    # Give it a moment to fully cancel
    await asyncio.sleep(0.01)
    assert first_task.cancelled() or first_task.done()


@pytest.mark.asyncio
async def test_focus_mode_resets_timestamp_on_first_utterance():
    """Turn start timestamp is set on first utterance."""
    aggregator = TurnAggregator(mode=Mode.FOCUS)
    ts = time.time()

    assert aggregator.turn_start_ts is None

    await aggregator.add_utterance("First", ts)
    assert aggregator.turn_start_ts == ts

    # Subsequent utterances don't change turn_start_ts
    await aggregator.add_utterance("Second", ts + 0.1)
    assert aggregator.turn_start_ts == ts


@pytest.mark.asyncio
async def test_callback_invocation():
    """Test that on_turn_complete callback is invoked."""
    callback_called = asyncio.Event()
    callback_data = {}

    async def callback(text: str, start_ts: float, end_ts: float, buffer: list[dict]):
        callback_data["text"] = text
        callback_data["start_ts"] = start_ts
        callback_data["end_ts"] = end_ts
        callback_data["buffer"] = buffer
        callback_called.set()

    aggregator = TurnAggregator(
        mode=Mode.FOCUS,
        focus_timeout=0.3,  # Short timeout for testing
        on_turn_complete=callback,
    )
    ts = time.time()

    # Add utterance
    await aggregator.add_utterance("Test utterance", ts)

    # Wait for timeout and callback
    await asyncio.wait_for(callback_called.wait(), timeout=1.0)

    assert callback_data["text"] == "Test utterance"
    assert callback_data["start_ts"] == ts
    assert "end_ts" in callback_data
    assert len(callback_data["buffer"]) == 1
    assert callback_data["buffer"][0]["text"] == "Test utterance"


@pytest.mark.asyncio
async def test_empty_buffer_on_mode_switch():
    """Buffer starts empty and stays empty until first utterance."""
    aggregator = TurnAggregator(mode=Mode.FOCUS)

    assert len(aggregator.buffer) == 0
    assert aggregator.turn_start_ts is None
    assert aggregator.last_utterance_ts is None

    # Mode switch without any utterances
    aggregator.set_mode(Mode.AMBIENT)

    # Should remain empty
    assert len(aggregator.buffer) == 0
    assert aggregator.turn_start_ts is None
    assert aggregator.last_utterance_ts is None


@pytest.mark.asyncio
async def test_multiple_utterances_within_timeout():
    """Multiple rapid utterances are buffered correctly."""
    aggregator = TurnAggregator(mode=Mode.AMBIENT)
    ts = time.time()

    # Add 5 utterances rapidly (within 3s timeout)
    texts = []
    for i in range(5):
        text = f"Utterance {i}"
        texts.append(text)
        should_submit, aggregated = await aggregator.add_utterance(text, ts + i * 0.1)
        assert not should_submit, f"Should not submit during rapid utterances (i={i})"

    # All should be buffered
    assert len(aggregator.buffer) == 5
    for i, item in enumerate(aggregator.buffer):
        assert item["text"] == texts[i]


# ---------------------------------------------------------------------------
# US-008: Acceptance criteria verification tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_focus_mode_0_5s_timeout_verified():
    """US-008: Verify focus mode timeout is exactly 0.5s with explicit assertion."""
    aggregator = TurnAggregator(mode=Mode.FOCUS, focus_timeout=0.5)
    ts = time.time()

    # Add utterance
    should_submit, aggregated = await aggregator.add_utterance("Test", ts)
    assert not should_submit
    assert aggregator.focus_timeout == 0.5, "Focus timeout must be 0.5s"

    # Wait just under timeout - should not submit
    await asyncio.sleep(0.45)
    assert len(aggregator.buffer) == 1, "Should still be buffered at 0.45s"

    # Wait just over timeout - should submit
    await asyncio.sleep(0.1)
    assert len(aggregator.buffer) == 0, "Buffer should be cleared after 0.5s timeout"


@pytest.mark.asyncio
async def test_ambient_mode_3_0s_timeout_verified():
    """US-008: Verify ambient mode timeout is exactly 3.0s with explicit assertion."""
    aggregator = TurnAggregator(mode=Mode.AMBIENT, ambient_timeout=3.0)

    # Add first utterance with current time
    should_submit, aggregated = await aggregator.add_utterance("First", time.time())
    assert not should_submit
    assert aggregator.ambient_timeout == 3.0, "Ambient timeout must be 3.0s"

    # Wait 2.5s - should still be buffered
    await asyncio.sleep(2.5)
    assert len(aggregator.buffer) == 1, "Should still be buffered at 2.5s"

    # Add second utterance with current time - resets timeout
    should_submit, aggregated = await aggregator.add_utterance("Second", time.time())
    assert not should_submit
    assert len(aggregator.buffer) == 2, "Should have 2 utterances buffered"

    # Wait for timeout to trigger (3.0s + small margin)
    # The timeout handler checks time.time() - last_utterance_ts >= timeout
    await asyncio.sleep(3.2)
    assert len(aggregator.buffer) == 0, "Buffer should be cleared after 3.0s timeout"
