"""BCDE-003: AWAITING_CONFIRMATION cancels via background task even if no frames arrive."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pipecat.frames.frames")

from src.config import DeciderState, Mode, Settings  # noqa: E402
from src.context import ContextBuilder  # noqa: E402
from src.decider import create_decider_processor  # noqa: E402
from src.storage import TranscriptStore  # noqa: E402


class FakeClaudeCLI:
    def __init__(self, decisions: list[dict[str, Any]]) -> None:
        self._decisions = list(decisions)
        self.call_action = AsyncMock(return_value={"summary": "done"})

    async def call_decider(self, prompt: str) -> dict[str, Any]:
        return self._decisions.pop(0)


@pytest.fixture
async def harness():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "heare.db"
        store = TranscriptStore(db)
        await store.init()
        settings = Settings()
        settings.mode = Mode.AMBIENT
        settings.confirmation_timeout_seconds = 1
        ctx = ContextBuilder(store, settings)
        try:
            yield store, settings, ctx
        finally:
            await store.close()


def _act_decision() -> dict[str, Any]:
    return {
        "type": "act",
        "confidence": 0.9,
        "reason": "asked",
        "intent": "run pytest",
        "action": {"tool": "Bash", "args": "pytest"},
    }


async def test_silent_timeout_cancels_without_frame(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI([_act_decision()])
    decider = create_decider_processor(cli, store, ctx, settings, "p {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    await decider._handle_listening("Гава, запусти тести")
    assert decider.state == DeciderState.AWAITING_CONFIRMATION
    assert decider._timeout_task is not None

    await asyncio.sleep(1.3)

    assert decider.state == DeciderState.LISTENING
    assert decider.pending_action is None
    cli.call_action.assert_not_awaited()


async def _drain(task: asyncio.Task | None) -> None:
    if task is None:
        return
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


async def test_yes_cancels_timeout_task(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI([_act_decision()])
    decider = create_decider_processor(cli, store, ctx, settings, "p {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    await decider._handle_listening("Гава, запусти тести")
    timeout_task = decider._timeout_task
    assert timeout_task is not None
    await decider._handle_confirmation("гава так")
    assert decider.state == DeciderState.LISTENING
    cli.call_action.assert_awaited()
    await _drain(timeout_task)
    assert timeout_task.cancelled() or timeout_task.done()


async def test_no_cancels_timeout_task(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI([_act_decision()])
    decider = create_decider_processor(cli, store, ctx, settings, "p {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    await decider._handle_listening("Гава, запусти тести")
    timeout_task = decider._timeout_task
    await decider._handle_confirmation("гава ні")
    assert decider.state == DeciderState.LISTENING
    cli.call_action.assert_not_awaited()
    assert timeout_task is not None
    await _drain(timeout_task)
    assert timeout_task.cancelled() or timeout_task.done()
