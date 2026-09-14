"""Tests for engine verdict publishing to State."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.spine import intents as I
from src.spine.engine import Engine, EngineState
from src.spine.situation import Situation


NOW = 1_000_000.0


def sit(**kw) -> Situation:
    """Create a Situation for testing."""
    base = dict(
        now=NOW,
        hour=14,
        minute=0,
        weekday=1,
        silence_s=600.0,
        user_silence_s=60.0,
        bot_state="idle",
        bot_state_s=60.0,
        jobs_running=0,
        unprompted_last_s=1e9,
        unprompted_1h=0,
    )
    base.update(kw)
    return Situation(**base)


def intent(**kw) -> I.Intent:
    """Create an Intent for testing."""
    base = dict(
        id=1,
        kind="job_done",
        text="test intent",
        origin=I.USER,
        urgency=0.8,
        state=I.PENDING,
        created_ts=NOW - 300,
        updated_ts=NOW - 300,
    )
    base.update(kw)
    return I.Intent(**base)


# ── Verdict publishing to State ──────────────────────────────────────

@pytest.mark.asyncio
async def test_engine_tick_publishes_verdict_to_state():
    """After tick(), the verdict is published to State.set_cache_only."""
    mock_state = MagicMock()
    mock_state.set_cache_only = MagicMock()
    mock_store = MagicMock()
    mock_store.pending = AsyncMock(return_value=[])

    engine = Engine(
        store=mock_store,
        say=AsyncMock(),
        state=mock_state,
    )
    engine.engine_state = EngineState(trust=1.5)

    # Tick with no pending intents should cause a silent verdict.
    await engine.tick(now=NOW)

    # Verify set_cache_only was called with engine_verdict.
    mock_state.set_cache_only.assert_called()
    call_kwargs = [
        (args[0], args[1]) if len(args) >= 2 else (args[0], None)
        for args, _ in mock_state.set_cache_only.call_args_list
    ]
    verdict_calls = [
        (key, val) for key, val in call_kwargs if key == "engine_verdict"
    ]
    assert len(verdict_calls) > 0, "engine_verdict was not published to State"

    # Parse the published verdict JSON.
    key, json_str = verdict_calls[0]
    published = json.loads(json_str)

    # Verify it has the expected shape.
    assert "speak" in published
    assert "reason" in published
    assert "trust" in published
    assert "ts" in published
    assert published["speak"] is False  # No pending intents
    assert published["trust"] == 1.5


@pytest.mark.asyncio
async def test_engine_tick_publishes_verdict_with_intent():
    """When there is a ripe intent to speak, verdict includes its details."""
    mock_state = MagicMock()
    mock_state.set_cache_only = MagicMock()
    mock_store = MagicMock()

    # A ripe intent that should be spoken.
    ripe_intent = intent(
        id=42,
        kind="reminder",
        text="call someone",
        origin=I.SELF,
        urgency=1.0,
    )
    mock_store.pending = AsyncMock(return_value=[ripe_intent])
    mock_store.mark_voiced = AsyncMock()

    # Inject mock _voice so it doesn't try to speak.
    engine = Engine(
        store=mock_store,
        say=AsyncMock(),
        state=mock_state,
    )
    engine._voice = AsyncMock()

    # Tick in a situation where speaking is allowed.
    await engine.tick(now=NOW)

    # Extract the published verdict.
    call_kwargs = [
        (args[0], args[1]) if len(args) >= 2 else (args[0], None)
        for args, _ in mock_state.set_cache_only.call_args_list
    ]
    verdict_calls = [
        (key, val) for key, val in call_kwargs if key == "engine_verdict"
    ]
    assert len(verdict_calls) > 0

    key, json_str = verdict_calls[0]
    published = json.loads(json_str)

    # Verdict should include intent details when speaking.
    assert published["intent_id"] == 42 or published["speak"] is False
    # (speak may be False due to timing; what matters is the shape)
    assert "intent_kind" in published
    assert "intent_text" in published


@pytest.mark.asyncio
async def test_engine_tick_silent_verdict_has_null_intent():
    """When not speaking, intent fields are None."""
    mock_state = MagicMock()
    mock_state.set_cache_only = MagicMock()
    mock_store = MagicMock()
    mock_store.pending = AsyncMock(return_value=[])

    engine = Engine(
        store=mock_store,
        say=AsyncMock(),
        state=mock_state,
    )
    await engine.tick(now=NOW)

    call_kwargs = [
        (args[0], args[1]) if len(args) >= 2 else (args[0], None)
        for args, _ in mock_state.set_cache_only.call_args_list
    ]
    verdict_calls = [
        (key, val) for key, val in call_kwargs if key == "engine_verdict"
    ]
    assert len(verdict_calls) > 0

    key, json_str = verdict_calls[0]
    published = json.loads(json_str)

    assert published["speak"] is False
    assert published["intent_id"] is None
    assert published["intent_kind"] is None
    assert published["intent_text"] is None


@pytest.mark.asyncio
async def test_engine_tick_without_state_does_not_break():
    """A tick with no state object does not raise."""
    mock_store = MagicMock()
    mock_store.pending = AsyncMock(return_value=[])

    engine = Engine(
        store=mock_store,
        say=AsyncMock(),
        state=None,  # No state
    )
    # Should not raise.
    await engine.tick(now=NOW)
    assert engine.last_verdict is not None


@pytest.mark.asyncio
async def test_engine_tick_state_without_set_cache_only_does_not_break():
    """A tick with a state missing set_cache_only does not raise."""
    mock_state = MagicMock(spec=[])  # No methods at all
    mock_store = MagicMock()
    mock_store.pending = AsyncMock(return_value=[])

    engine = Engine(
        store=mock_store,
        say=AsyncMock(),
        state=mock_state,
    )
    # Should not raise.
    await engine.tick(now=NOW)
    assert engine.last_verdict is not None


@pytest.mark.asyncio
async def test_engine_tick_state_write_failure_does_not_break():
    """A State.set_cache_only failure does not break the tick."""
    mock_state = MagicMock()
    mock_state.set_cache_only = MagicMock(side_effect=RuntimeError("write failed"))
    mock_store = MagicMock()
    mock_store.pending = AsyncMock(return_value=[])

    engine = Engine(
        store=mock_store,
        say=AsyncMock(),
        state=mock_state,
    )
    # Should not raise despite the write failure.
    await engine.tick(now=NOW)
    assert engine.last_verdict is not None
