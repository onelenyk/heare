"""Tests for DeciderProcessor state transitions using a fake ClaudeCLI.

We construct the DeciderProcessor via create_decider_processor which pulls
the pipecat base classes at call time. These tests skip themselves if
pipecat is not importable so the suite stays runnable on lean environments.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

pipecat = pytest.importorskip("pipecat.frames.frames")
TranscriptionFrame = pipecat.TranscriptionFrame

from src.config import DeciderState, Mode, Settings  # noqa: E402
from src.context import ContextBuilder  # noqa: E402
from src.decider import create_decider_processor  # noqa: E402
from src.storage import TranscriptStore  # noqa: E402


class FakeClaudeCLI:
    def __init__(self, decisions: list[dict[str, Any]]) -> None:
        self._decisions = decisions
        self.call_action = AsyncMock(return_value={"summary": "ok, done"})
        self.calls: list[str] = []

    async def call_decider(self, prompt: str) -> dict[str, Any]:
        self.calls.append(prompt)
        return self._decisions.pop(0)


def _make_frame(text: str):
    try:
        return TranscriptionFrame(text=text, user_id="u", timestamp="t")
    except TypeError:
        return TranscriptionFrame(user_id="u", timestamp="t", text=text)


@pytest.fixture
async def harness():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "heare.db"
        store = TranscriptStore(db)
        await store.init()
        settings = Settings()
        settings.mode = Mode.AMBIENT
        settings.confirmation_timeout_seconds = 1
        ctx_builder = ContextBuilder(store, settings)
        try:
            yield store, settings, ctx_builder
        finally:
            await store.close()


async def test_listening_to_speak(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI(
        [{"type": "speak", "confidence": 0.95, "reply": "привіт", "reason": "greeted"}]
    )
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening("привіт")
    assert decider.state == DeciderState.LISTENING
    assert decider.push_frame.await_count == 1


async def test_listening_to_nothing(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI([{"type": "nothing", "reason": "not for me"}])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening("випадкова фраза")
    assert decider.state == DeciderState.LISTENING
    assert decider.push_frame.await_count == 0


async def test_listening_to_awaiting_confirmation(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI(
        [
            {
                "type": "act",
                "confidence": 0.9,
                "reason": "asked to run tests",
                "intent": "run pytest",
                "action": {"tool": "Bash", "args": "pytest"},
            }
        ]
    )
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening("запусти тести")
    assert decider.state == DeciderState.AWAITING_CONFIRMATION
    assert decider.pending_action is not None


async def test_confirmation_yes_executes(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI(
        [
            {
                "type": "act",
                "confidence": 0.9,
                "reason": "asked",
                "intent": "run pytest",
                "action": {"tool": "Bash", "args": "pytest"},
            }
        ]
    )
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening("запусти тести")
    await decider._handle_confirmation("так")
    cli.call_action.assert_awaited()
    assert decider.state == DeciderState.LISTENING
    assert decider.pending_action is None


async def test_confirmation_no_cancels(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI(
        [
            {
                "type": "act",
                "confidence": 0.9,
                "reason": "asked",
                "intent": "run pytest",
                "action": {"tool": "Bash", "args": "pytest"},
            }
        ]
    )
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening("запусти тести")
    await decider._handle_confirmation("ні")
    cli.call_action.assert_not_awaited()
    assert decider.state == DeciderState.LISTENING
    assert decider.pending_action is None


async def test_heartbeat_skipped_during_confirmation(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI(
        [
            {
                "type": "act",
                "confidence": 0.9,
                "reason": "asked",
                "intent": "run pytest",
                "action": {"tool": "Bash", "args": "pytest"},
            },
        ]
    )
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening("запусти тести")
    assert decider.state == DeciderState.AWAITING_CONFIRMATION
    await decider.on_heartbeat_tick()
    assert decider.state == DeciderState.AWAITING_CONFIRMATION
    assert cli.calls == [cli.calls[0]]
