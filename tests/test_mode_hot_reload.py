"""BCDE-002: mode hot-reload takes effect on a running DeciderProcessor."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pipecat.frames.frames")

from src.config import Mode, Settings  # noqa: E402
from src.context import ContextBuilder  # noqa: E402
from src.decider import create_decider_processor  # noqa: E402
from src.storage import TranscriptStore  # noqa: E402


class FakeClaudeCLI:
    def __init__(self, decisions: list[dict[str, Any]]) -> None:
        self._decisions = list(decisions)
        self.calls: list[str] = []
        self.call_action = AsyncMock(return_value={"summary": "done"})

    async def call_decider(self, prompt: str) -> dict[str, Any]:
        self.calls.append(prompt)
        if not self._decisions:
            return {"type": "nothing"}
        return self._decisions.pop(0)


@pytest.fixture
async def harness():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db = tmp_path / "heare.db"
        mode_file = tmp_path / "mode"
        store = TranscriptStore(db)
        await store.init()
        settings = Settings()
        settings.mode_file = mode_file
        settings.mode = Mode.AMBIENT
        ctx_builder = ContextBuilder(store, settings)
        try:
            yield store, settings, ctx_builder, mode_file
        finally:
            await store.close()


async def test_mode_hot_reload_ambient_to_silent(harness) -> None:
    store, settings, ctx, mode_file = harness
    cli = FakeClaudeCLI([{"type": "speak", "confidence": 0.95, "reply": "hi"}])
    decider = create_decider_processor(cli, store, ctx, settings, "p {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    mode_file.write_text("silent")
    decider._reload_mode()
    assert settings.mode == Mode.SILENT

    await decider._store_only("one")
    assert cli.calls == []

    mode_file.write_text("ambient")
    decider._reload_mode()
    assert settings.mode == Mode.AMBIENT
    await decider._handle_listening("два")
    assert len(cli.calls) == 1


async def test_reload_mode_ignores_missing_file(harness) -> None:
    store, settings, ctx, mode_file = harness
    settings.mode = Mode.FOCUS
    decider = create_decider_processor(
        FakeClaudeCLI([]), store, ctx, settings, "p {mode}"
    )
    decider._reload_mode()
    assert settings.mode == Mode.FOCUS


async def test_reload_mode_ignores_garbage(harness) -> None:
    store, settings, ctx, mode_file = harness
    mode_file.write_text("not-a-real-mode")
    settings.mode = Mode.AMBIENT
    decider = create_decider_processor(
        FakeClaudeCLI([]), store, ctx, settings, "p {mode}"
    )
    decider._reload_mode()
    assert settings.mode == Mode.AMBIENT
