"""Tests for WebRTCAECFilter (Step 3: WebRTC AEC3 acoustic echo cancellation).

Run RED (expected to fail without implementation), then implement GREEN.
"""
from __future__ import annotations

import time
from typing import Any

import numpy as np
import pytest

class MockEchoState:
    def __init__(
        self, bot_speaking: bool = False, buffer: np.ndarray | None = None
    ) -> None:
        self._bot_speaking = bot_speaking
        self._bot_stopped_at: float = 0.0
        self._buffer = buffer if buffer is not None else np.zeros(16000, dtype=np.float32)

    @property
    def bot_speaking(self) -> bool:
        return self._bot_speaking

    @property
    def bot_stopped_at(self) -> float:
        return self._bot_stopped_at

    def get_buffer(self) -> np.ndarray:
        return self._buffer.copy()

    def set_bot_speaking(self, speaking: bool) -> None:
        self._bot_speaking = speaking
        if not speaking:
            self._bot_stopped_at = time.monotonic()


def test_aec_filter_import() -> None:
    """WebRTCAECFilter can be imported (deferred pipecat imports)."""
    from src.pipeline.stages.webrtc_aec_filter import WebRTCAECFilter

    assert WebRTCAECFilter is not None


def test_aec_filter_requires_ref_for_active() -> None:
    """_is_active returns False when bot not speaking and no cooldown."""
    from src.pipeline.stages.webrtc_aec_filter import WebRTCAECFilter

    echo_state = MockEchoState(bot_speaking=False)
    filt = WebRTCAECFilter(echo_state=echo_state, cooldown_seconds=0.0)
    assert filt._is_active() is False


def test_aec_filter_is_active_when_speaking() -> None:
    """_is_active returns True when bot is speaking."""
    from src.pipeline.stages.webrtc_aec_filter import WebRTCAECFilter

    echo_state = MockEchoState(bot_speaking=True)
    filt = WebRTCAECFilter(echo_state=echo_state, cooldown_seconds=0.5)
    assert filt._is_active() is True


def test_aec_filter_active_during_cooldown() -> None:
    """_is_active returns True during cooldown window after bot stops."""
    from src.pipeline.stages.webrtc_aec_filter import WebRTCAECFilter

    echo_state = MockEchoState(bot_speaking=True)
    filt = WebRTCAECFilter(echo_state=echo_state, cooldown_seconds=5.0)
    assert filt._is_active() is True
    echo_state.set_bot_speaking(False)
    assert filt._is_active() is True


pipecat = pytest.importorskip("pipecat.frames.frames")
InputAudioRawFrame = pipecat.InputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402


def _capture(processor: Any) -> list[tuple[Any, Any]]:
    """Replace push_frame with a fake that records calls."""
    pushed: list[tuple[Any, Any]] = []

    async def fake_push(frame: Any, direction: Any = None) -> None:
        pushed.append((frame, direction))

    processor.push_frame = fake_push  # type: ignore[assignment]
    return pushed


async def test_aec_filter_processes_when_active() -> None:
    """Frame audio is modified when bot is speaking (AEC processor called)."""
    from src.pipeline.stages.webrtc_aec_filter import WebRTCAECFilter

    class _FakeProcessor:
        def process(self, near: np.ndarray, far: np.ndarray) -> np.ndarray:
            return near * 0.5

    buffer = np.sin(np.linspace(0, 2 * np.pi, 1600)).astype(np.float32) * 0.5
    echo_state = MockEchoState(bot_speaking=True, buffer=buffer)
    filt = WebRTCAECFilter(echo_state=echo_state)
    pushed = _capture(filt)

    filt._processor = _FakeProcessor()

    mic = buffer[:1600] * 0.8
    mic_s16 = (mic * 32767).astype(np.int16).tobytes()
    frame = InputAudioRawFrame(audio=mic_s16, sample_rate=16000, num_channels=1)

    await filt.process_frame(frame, FrameDirection.DOWNSTREAM)

    input_frames = [f for f, _ in pushed if isinstance(f, InputAudioRawFrame)]
    assert len(input_frames) == 1, "frame should be pushed downstream"
    processed_audio = input_frames[0].audio
    assert processed_audio != mic_s16, "frame audio should be modified by AEC"

    orig_samples = np.frombuffer(mic_s16, dtype=np.int16).astype(np.float32)
    proc_samples = np.frombuffer(processed_audio, dtype=np.int16).astype(np.float32)
    assert np.abs(proc_samples).mean() < np.abs(orig_samples).mean() * 0.6


async def test_aec_filter_passthrough_when_inactive() -> None:
    """Frame passes through unchanged when bot not speaking."""
    from src.pipeline.stages.webrtc_aec_filter import WebRTCAECFilter

    echo_state = MockEchoState(bot_speaking=False)
    filt = WebRTCAECFilter(echo_state=echo_state, cooldown_seconds=0.0)
    pushed = _capture(filt)

    signal = np.sin(np.linspace(0, 10 * np.pi, 3200)).astype(np.float32)
    pcm_s16 = (signal * 16000).astype(np.int16).tobytes()
    frame = InputAudioRawFrame(audio=pcm_s16, sample_rate=16000, num_channels=1)

    await filt.process_frame(frame, FrameDirection.DOWNSTREAM)

    input_frames = [f for f, _ in pushed if isinstance(f, InputAudioRawFrame)]
    assert len(input_frames) == 1
    assert input_frames[0].audio == pcm_s16, "audio should be unmodified when inactive"


def test_aec_filter_in_stage_assembly() -> None:
    """AEC filter can be passed to _assemble_native_stages and appears after echo_gate."""
    from src.pipeline.build import _assemble_native_stages

    stages = _assemble_native_stages(
        transport_input="INPUT",
        transport_output="OUTPUT",
        stt="STT",
        stt_error_observer="STT_ERR",
        transcription_gate="GATE",
        user_aggregator="USER_AGG",
        llm_service="LLM",
        tts="TTS",
        assistant_aggregator="ASSIST_AGG",
        echo_gate="ECHO_GATE",
        aec_filter="AEC_FILTER",
        sidetone="SIDETONE",
    )
    # Verify ordering: echo_gate → aec_filter → sidetone → STT
    echo_idx = stages.index("ECHO_GATE")
    aec_idx = stages.index("AEC_FILTER")
    sidetone_idx = stages.index("SIDETONE")
    stt_idx = stages.index("STT")
    assert echo_idx < aec_idx, "AEC filter should come AFTER echo_gate"
    assert aec_idx < sidetone_idx, "AEC filter should come BEFORE sidetone"
    assert sidetone_idx < stt_idx, "Sidetone should come before STT"


async def test_aec_filter_short_frame_passthrough() -> None:
    """Frames shorter than 160 samples (10 ms) bypass AEC processing."""
    from src.pipeline.stages.webrtc_aec_filter import WebRTCAECFilter

    buffer = np.sin(np.linspace(0, 0.1 * np.pi, 160)).astype(np.float32) * 0.5
    echo_state = MockEchoState(bot_speaking=True, buffer=buffer)
    filt = WebRTCAECFilter(echo_state=echo_state)
    pushed = _capture(filt)

    filt._processor = object()

    mic = np.zeros(80, dtype=np.int16)
    frame = InputAudioRawFrame(audio=mic.tobytes(), sample_rate=16000, num_channels=1)

    await filt.process_frame(frame, FrameDirection.DOWNSTREAM)

    input_frames = [f for f, _ in pushed if isinstance(f, InputAudioRawFrame)]
    assert len(input_frames) == 1
    assert input_frames[0].audio == mic.tobytes(), \
        "frames < 160 samples should pass through unmodified"
