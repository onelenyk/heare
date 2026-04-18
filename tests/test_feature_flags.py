"""Tests for feature flags controlling turn aggregation and conversation memory.

These tests verify that the feature flags work correctly and that features can be
rolled out gradually.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import Mode, Settings
from src.context import ContextBuilder
from src.decider import create_decider_processor
from src.storage import TranscriptStore


class FakeClaudeCLI:
    def __init__(self, decisions: list[dict[str, Any]]) -> None:
        self._decisions = decisions
        self.call_action = AsyncMock(return_value={"summary": "ok, done"})
        self.calls: list[str] = []

    async def call_decider(self, prompt: str) -> dict[str, Any]:
        self.calls.append(prompt)
        return self._decisions.pop(0)

    @property
    def persona(self) -> str | None:
        return "test"


@pytest.fixture
async def harness():
    """Create test harness with database and settings."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "heare.db"
        store = TranscriptStore(db)
        await store.init()
        settings = Settings()
        ctx_builder = ContextBuilder(store, settings)
        try:
            yield store, settings, ctx_builder
        finally:
            await store.close()


async def test_aggregation_disabled_baseline(harness) -> None:
    """US-010: Verify default behavior with both flags disabled (baseline)."""
    store, settings, ctx = harness

    # Default config: both features disabled
    assert settings.turn_aggregation_enabled is False, (
        "turn_aggregation_enabled should default to False"
    )
    assert settings.conversation_memory_enabled is False, (
        "conversation_memory_enabled should default to False"
    )

    cli = FakeClaudeCLI([{"type": "nothing", "reason": "ignore"}])
    decider = create_decider_processor(cli, store, ctx, settings, "Mode: {mode}\nTranscript: {transcript_or_heartbeat}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    # Send 3 utterances with wake words to bypass quick-nothing filter
    await decider._handle_listening("Гава, first utterance")
    await decider._handle_listening("Гава, second utterance")
    await decider._handle_listening("Гава, third utterance")

    # Baseline: each utterance triggers a separate decider call (no aggregation)
    assert len(cli.calls) == 3, (
        f"Baseline should have 3 separate calls (no aggregation), got {len(cli.calls)}"
    )

    # Verify turn aggregator and conversation manager are None
    assert decider.turn_aggregator is None, "turn_aggregator should be None when disabled"
    assert decider.conversation_manager is None, "conversation_manager should be None when disabled"


async def test_aggregation_only_no_memory(harness) -> None:
    """US-010: Turn aggregation enabled, conversation memory disabled."""
    from src.turn_aggregator import TurnAggregator

    store, settings, ctx = harness

    # Enable only turn aggregation
    settings.turn_aggregation_enabled = True
    settings.conversation_memory_enabled = False

    assert settings.turn_aggregation_enabled is True
    assert settings.conversation_memory_enabled is False

    cli = FakeClaudeCLI([{"type": "nothing", "reason": "ignore"}])

    # Create turn aggregator
    turn_aggregator = TurnAggregator(
        mode=Mode.AMBIENT,
        focus_timeout=0.5,
        ambient_timeout=3.0,
    )

    decider = create_decider_processor(
        cli, store, ctx, settings, "Mode: {mode}\nTranscript: {transcript_or_heartbeat}",
        turn_aggregator=turn_aggregator,
        conversation_manager=None,  # No conversation memory
    )
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    # Send 3 rapid utterances - should be buffered
    await decider._handle_listening("Гава, перше")
    await decider._handle_listening("Гава, друге")
    await decider._handle_listening("Гава, третє")

    # With aggregation: calls should be delayed/buffered
    # (might be 0 if timeout hasn't elapsed yet, or 1 if it has)
    assert len(cli.calls) <= 1, (
        f"With aggregation, should have 0-1 call (buffered), got {len(cli.calls)}"
    )

    # Verify turn aggregator is present
    assert decider.turn_aggregator is not None, "turn_aggregator should be present"
    assert decider.conversation_manager is None, "conversation_manager should be None"


async def test_both_enabled(harness) -> None:
    """US-010: Both features enabled, verify integration works."""
    from src.turn_aggregator import TurnAggregator
    from src.conversation import ConversationManager

    store, settings, ctx = harness

    # Enable both features
    settings.turn_aggregation_enabled = True
    settings.conversation_memory_enabled = True

    assert settings.turn_aggregation_enabled is True
    assert settings.conversation_memory_enabled is True

    # Create turn aggregator
    turn_aggregator = TurnAggregator(
        mode=Mode.AMBIENT,
        focus_timeout=0.5,
        ambient_timeout=3.0,
    )

    # Create conversation manager
    claude_mock = MagicMock()
    conversation_manager = ConversationManager(store, claude_mock)

    # Mock conversation manager methods
    async def mock_extract_topics(text: str) -> list[str]:
        return ["test topic"]

    conversation_manager.extract_topics = mock_extract_topics

    async def mock_get_or_create_active() -> int:
        return 42

    conversation_manager.get_or_create_active = mock_get_or_create_active

    async def mock_update_summary(conv_id: int, text: str, topics: list[str]) -> None:
        pass

    conversation_manager.update_summary = mock_update_summary

    async def mock_build_context(conv_id: int) -> dict:
        return {
            "conversation_active": True,
            "conversation_summary": "Test conversation",
            "active_topics": ["test topic"],
            "entities": {},
            "recent_turns": [],
        }

    conversation_manager.build_context = mock_build_context

    cli = FakeClaudeCLI([{"type": "nothing", "reason": "ignore"}])

    decider = create_decider_processor(
        cli, store, ctx, settings, "Mode: {mode}\nTranscript: {transcript_or_heartbeat}",
        turn_aggregator=turn_aggregator,
        conversation_manager=conversation_manager,
    )
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    # Send utterances - should be buffered AND conversation context tracked
    await decider._handle_listening("Гава, перше повідомлення")
    await decider._handle_listening("Гава, друге повідомлення")

    # Verify both integrations are present
    assert decider.turn_aggregator is not None, "turn_aggregator should be present"
    assert decider.conversation_manager is not None, "conversation_manager should be present"

    # With aggregation: calls should be buffered
    assert len(cli.calls) <= 1, (
        f"With both features, should have 0-1 call (buffered), got {len(cli.calls)}"
    )


async def test_feature_flags_can_be_flipped_independently(harness) -> None:
    """US-010: Verify flags can be enabled/disabled independently."""
    store, settings, ctx = harness

    # Test all 4 combinations
    combinations = [
        (False, False),  # Both off (baseline)
        (True, False),   # Aggregation only
        (False, True),   # Memory only
        (True, True),    # Both on
    ]

    for turn_agg, conv_mem in combinations:
        # Update settings
        settings.turn_aggregation_enabled = turn_agg
        settings.conversation_memory_enabled = conv_mem

        # Verify settings took effect
        assert settings.turn_aggregation_enabled == turn_agg
        assert settings.conversation_memory_enabled == conv_mem

        # Create decider (should not crash regardless of flag combination)
        cli = FakeClaudeCLI([{"type": "nothing", "reason": "ignore"}])
        decider = create_decider_processor(
            cli, store, ctx, settings, "Mode: {mode}\nTranscript: {transcript_or_heartbeat}"
        )
        decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

        # Should be able to process utterances without crashing
        await decider._handle_listening("Test utterance")


async def test_default_settings_match_plan_spec(harness) -> None:
    """US-010: Verify default settings match the plan specification."""
    store, settings, ctx = harness

    # Per plan: "Default config has turn_aggregation_enabled=false, conversation_memory_enabled=false"
    default_settings = Settings()

    assert default_settings.turn_aggregation_enabled is False, (
        "turn_aggregation_enabled should default to False per plan spec"
    )
    assert default_settings.conversation_memory_enabled is False, (
        "conversation_memory_enabled should default to False per plan spec"
    )

    # Verify timeout defaults match plan spec
    assert default_settings.focus_mode_turn_timeout == 0.5, (
        "focus_mode_turn_timeout should default to 0.5s per plan spec"
    )
    assert default_settings.ambient_mode_turn_timeout == 3.0, (
        "ambient_mode_turn_timeout should default to 3.0s per plan spec"
    )
    assert default_settings.max_turn_duration == 30.0, (
        "max_turn_duration should default to 30.0s per plan spec"
    )


def test_generator_mode_default_is_false() -> None:
    """Phase 1 US-P1-08: generator_mode defaults to False — emergency opt-in only."""
    s = Settings()
    assert s.generator_mode is False


def test_generator_mode_both_values_settable() -> None:
    """Phase 1 US-P1-08: flag toggles without side-effects at Settings layer."""
    s = Settings()
    s.generator_mode = True
    assert s.generator_mode is True
    s.generator_mode = False
    assert s.generator_mode is False
