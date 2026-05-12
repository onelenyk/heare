"""Tests for src/pipeline/stages/cancel_flag_gate.py."""
from __future__ import annotations

from pathlib import Path

import pytest


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

    flag = tmp_path / "cancel.flag"  # not created → no cancel
    proc = create_cancel_flag_gate(flag_path=flag)

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

    from src.pipeline.stages.cancel_flag_gate import (
        create_cancel_flag_gate,
        request_cancel,
    )

    flag = tmp_path / "cancel.flag"
    request_cancel(flag)
    proc = create_cancel_flag_gate(flag_path=flag)

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
    assert first_dir == FrameDirection.UPSTREAM
    assert second_frame is frame
    # Flag is consumed.
    assert not flag.exists()


@pytest.mark.asyncio
async def test_gate_refires_after_flag_recreated(tmp_path: Path):
    from pipecat.frames.frames import InterruptionFrame, TextFrame

    from src.pipeline.stages.cancel_flag_gate import (
        create_cancel_flag_gate,
        request_cancel,
    )

    flag = tmp_path / "cancel.flag"
    request_cancel(flag)
    proc = create_cancel_flag_gate(flag_path=flag)

    captured: list = []

    async def fake_push(frame, direction=None):
        captured.append(frame)

    proc.push_frame = fake_push  # type: ignore[method-assign]

    await proc.process_frame(TextFrame(text="first"), None)
    # No flag now: subsequent frame should NOT push an InterruptionFrame.
    await proc.process_frame(TextFrame(text="second"), None)
    # Re-create the flag: third frame must push InterruptionFrame again.
    request_cancel(flag)
    await proc.process_frame(TextFrame(text="third"), None)

    interruptions = [f for f in captured if isinstance(f, InterruptionFrame)]
    assert len(interruptions) == 2
