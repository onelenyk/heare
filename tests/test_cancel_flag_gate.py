"""Tests for src/pipeline/stages/cancel_flag_gate.py."""
from __future__ import annotations

from pathlib import Path

import pytest


class _MockState:
    """Minimal State mock for testing cancel flag gate."""
    def __init__(self, **initial):
        self._data = dict(initial)

    def get_bool(self, key: str) -> bool:
        return self._data.get(key) == "1"

    def get(self, key: str, default: str = "") -> str:
        return self._data.get(key, default)

    async def set(self, key: str, value: str):
        self._data[key] = value

    async def set_bool(self, key: str, value: bool):
        self._data[key] = "1" if value else "0"


def test_request_cancel_creates_flag(tmp_path: Path):
    from src.pipeline.stages.cancel_flag_gate import request_cancel

    flag = tmp_path / "nested" / "cancel.flag"
    request_cancel(flag)
    assert flag.exists()


def test_request_cancel_idempotent(tmp_path: Path):
    from src.pipeline.stages.cancel_flag_gate import request_cancel

    flag = tmp_path / "cancel.flag"
    request_cancel(flag)
    request_cancel(flag)  # must not raise
    assert flag.exists()


@pytest.mark.asyncio
async def test_gate_passes_frame_when_flag_absent(tmp_path: Path):
    from pipecat.frames.frames import TextFrame

    from src.pipeline.stages.cancel_flag_gate import create_cancel_flag_gate

    state = _MockState()
    proc = create_cancel_flag_gate(state=state)

    captured: list = []

    async def fake_push(frame, direction=None):
        captured.append((frame, direction))

    proc.push_frame = fake_push  # type: ignore[method-assign]

    frame = TextFrame(text="hello")
    await proc.process_frame(frame, None)

    # Frame forwarded, nothing else pushed.
    assert len(captured) == 1
    assert captured[0][0] is frame


@pytest.mark.asyncio
async def test_gate_fires_interruption_when_flag_present(tmp_path: Path):
    from pipecat.frames.frames import InterruptionFrame, TextFrame
    from pipecat.processors.frame_processor import FrameDirection

    from src.pipeline.stages.cancel_flag_gate import create_cancel_flag_gate

    state = _MockState(cancel="1")
    proc = create_cancel_flag_gate(state=state)

    captured: list = []

    async def fake_push(frame, direction=None):
        captured.append((frame, direction))

    proc.push_frame = fake_push  # type: ignore[method-assign]

    frame = TextFrame(text="hello")
    await proc.process_frame(frame, None)

    # Two pushes: InterruptionFrame upstream, then the original frame.
    assert len(captured) == 2
    first_frame, first_dir = captured[0]
    second_frame, _ = captured[1]
    assert isinstance(first_frame, InterruptionFrame)
    assert first_dir == FrameDirection.DOWNSTREAM
    assert second_frame is frame
    # Flag is consumed (cleared to "0").
    assert state._data.get("cancel") == "0"


@pytest.mark.asyncio
async def test_gate_refires_after_flag_recreated(tmp_path: Path):
    from pipecat.frames.frames import InterruptionFrame, TextFrame

    from src.pipeline.stages.cancel_flag_gate import create_cancel_flag_gate

    state = _MockState(cancel="1")
    proc = create_cancel_flag_gate(state=state)

    captured: list = []

    async def fake_push(frame, direction=None):
        captured.append(frame)

    proc.push_frame = fake_push  # type: ignore[method-assign]

    await proc.process_frame(TextFrame(text="first"), None)
    # No flag now: subsequent frame should NOT push an InterruptionFrame.
    await proc.process_frame(TextFrame(text="second"), None)
    # Re-create the flag: third frame must push InterruptionFrame again.
    state._data["cancel"] = "1"
    await proc.process_frame(TextFrame(text="third"), None)

    interruptions = [f for f in captured if isinstance(f, InterruptionFrame)]
    assert len(interruptions) == 2
