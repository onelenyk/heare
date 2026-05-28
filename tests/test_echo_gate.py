"""Tests for the acoustic echo gate (EchoState + MicEchoGate + BotAudioCollector)."""
from __future__ import annotations

import time
from typing import Any

import numpy as np
import pytest

from src.pipeline.echo_state import EchoState


# ---------------------------------------------------------------------------
# EchoState ring buffer


def test_echo_state_add_and_get_buffer():
    state = EchoState(buffer_seconds=1.0, target_sample_rate=16000)
    pcm = np.sin(np.linspace(0, 2 * np.pi, 1600)).astype(np.float32)
    pcm_s16 = (pcm * 32767).astype(np.int16).tobytes()
    state.add_bot_audio(pcm_s16, source_sample_rate=16000)
    buf = state.get_buffer()
    assert len(buf) == 16000
    assert np.max(np.abs(buf[:1600])) > 0


def test_echo_state_resamples():
    state = EchoState(buffer_seconds=1.0, target_sample_rate=16000)
    pcm_24k = np.sin(np.linspace(0, 2 * np.pi, 2400)).astype(np.float32)
    pcm_s16 = (pcm_24k * 32767).astype(np.int16).tobytes()
    state.add_bot_audio(pcm_s16, source_sample_rate=24000)
    buf = state.get_buffer()
    assert len(buf) == 16000
    nonzero = np.count_nonzero(buf)
    assert nonzero > 100


def test_echo_state_clear():
    state = EchoState(buffer_seconds=0.5, target_sample_rate=16000)
    pcm = np.ones(800, dtype=np.float32)
    pcm_s16 = (pcm * 32767).astype(np.int16).tobytes()
    state.add_bot_audio(pcm_s16, source_sample_rate=16000)
    state.clear()
    assert np.all(state.get_buffer() == 0)


def test_echo_state_bot_speaking_flag():
    state = EchoState()
    assert not state.bot_speaking
    state.set_bot_speaking(True)
    assert state.bot_speaking
    state.set_bot_speaking(False)
    assert not state.bot_speaking
    assert state.bot_stopped_at > 0


# ---------------------------------------------------------------------------
# MicEchoGate + BotAudioCollector


pipecat = pytest.importorskip("pipecat.frames.frames")
InputAudioRawFrame = pipecat.InputAudioRawFrame
TTSAudioRawFrame = pipecat.TTSAudioRawFrame
BotStartedSpeakingFrame = pipecat.BotStartedSpeakingFrame
BotStoppedSpeakingFrame = pipecat.BotStoppedSpeakingFrame
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402

from src.config import Settings  # noqa: E402
from src.pipeline.stages.echo_gate import create_echo_gate_stages  # noqa: E402


def _capture(processor) -> list[tuple[Any, Any]]:
    pushed: list[tuple[Any, Any]] = []

    async def fake_push(frame, direction=None):
        pushed.append((frame, direction))

    processor.push_frame = fake_push  # type: ignore[assignment]
    return pushed


async def test_echo_gate_drops_correlated_audio():
    state = EchoState(buffer_seconds=1.0, target_sample_rate=16000)
    settings = Settings()
    settings.echo_gate_threshold = 0.3
    settings.echo_gate_cooldown_seconds = 0.5
    _, gate = create_echo_gate_stages(state, settings)
    pushed = _capture(gate)

    signal = np.sin(np.linspace(0, 10 * np.pi, 3200)).astype(np.float32)
    pcm_s16 = (signal * 16000).astype(np.int16).tobytes()
    state.add_bot_audio(pcm_s16, source_sample_rate=16000)
    state.set_bot_speaking(True)

    mic_frame = InputAudioRawFrame(
        audio=pcm_s16,
        sample_rate=16000,
        num_channels=1,
    )
    await gate.process_frame(mic_frame, FrameDirection.DOWNSTREAM)

    input_frames = [f for f, _ in pushed if isinstance(f, InputAudioRawFrame)]
    assert len(input_frames) == 0


async def test_echo_gate_passes_uncorrelated_audio():
    state = EchoState(buffer_seconds=1.0, target_sample_rate=16000)
    settings = Settings()
    settings.echo_gate_threshold = 0.3
    settings.echo_gate_cooldown_seconds = 0.5
    _, gate = create_echo_gate_stages(state, settings)
    pushed = _capture(gate)

    bot_signal = np.sin(np.linspace(0, 10 * np.pi, 3200)).astype(np.float32)
    bot_pcm = (bot_signal * 16000).astype(np.int16).tobytes()
    state.add_bot_audio(bot_pcm, source_sample_rate=16000)
    state.set_bot_speaking(True)

    noise = np.random.randn(3200).astype(np.float32) * 1000
    mic_pcm = noise.astype(np.int16).tobytes()
    mic_frame = InputAudioRawFrame(
        audio=mic_pcm,
        sample_rate=16000,
        num_channels=1,
    )
    await gate.process_frame(mic_frame, FrameDirection.DOWNSTREAM)

    input_frames = [f for f, _ in pushed if isinstance(f, InputAudioRawFrame)]
    assert len(input_frames) == 1


async def test_echo_gate_passes_when_bot_silent():
    state = EchoState(buffer_seconds=1.0, target_sample_rate=16000)
    settings = Settings()
    settings.echo_gate_threshold = 0.3
    settings.echo_gate_cooldown_seconds = 0.0
    _, gate = create_echo_gate_stages(state, settings)
    pushed = _capture(gate)

    signal = np.sin(np.linspace(0, 10 * np.pi, 3200)).astype(np.float32)
    pcm_s16 = (signal * 16000).astype(np.int16).tobytes()
    state.add_bot_audio(pcm_s16, source_sample_rate=16000)

    mic_frame = InputAudioRawFrame(
        audio=pcm_s16,
        sample_rate=16000,
        num_channels=1,
    )
    await gate.process_frame(mic_frame, FrameDirection.DOWNSTREAM)

    input_frames = [f for f, _ in pushed if isinstance(f, InputAudioRawFrame)]
    assert len(input_frames) == 1


async def test_collector_captures_tts_audio():
    state = EchoState(buffer_seconds=1.0, target_sample_rate=16000)
    settings = Settings()
    settings.echo_gate_threshold = 0.3
    settings.echo_gate_cooldown_seconds = 0.5
    collector, _ = create_echo_gate_stages(state, settings)
    pushed = _capture(collector)

    signal = np.sin(np.linspace(0, 10 * np.pi, 4800)).astype(np.float32)
    pcm_s16 = (signal * 16000).astype(np.int16).tobytes()
    tts_frame = TTSAudioRawFrame(
        audio=pcm_s16,
        sample_rate=24000,
        num_channels=1,
    )
    await collector.process_frame(tts_frame, FrameDirection.DOWNSTREAM)

    buf = state.get_buffer()
    assert np.count_nonzero(buf) > 100

    tts_frames = [f for f, _ in pushed if isinstance(f, TTSAudioRawFrame)]
    assert len(tts_frames) == 1


async def test_collector_tracks_bot_speaking():
    state = EchoState()
    settings = Settings()
    settings.echo_gate_threshold = 0.3
    settings.echo_gate_cooldown_seconds = 0.5
    collector, _ = create_echo_gate_stages(state, settings)
    _capture(collector)

    await collector.process_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    assert state.bot_speaking

    await collector.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    assert not state.bot_speaking
    assert state.bot_stopped_at > 0


async def test_echo_gate_cooldown():
    state = EchoState(buffer_seconds=1.0, target_sample_rate=16000)
    settings = Settings()
    settings.echo_gate_threshold = 0.3
    settings.echo_gate_cooldown_seconds = 0.5
    _, gate = create_echo_gate_stages(state, settings)
    pushed = _capture(gate)

    signal = np.sin(np.linspace(0, 10 * np.pi, 3200)).astype(np.float32)
    pcm_s16 = (signal * 16000).astype(np.int16).tobytes()
    state.add_bot_audio(pcm_s16, source_sample_rate=16000)
    state.set_bot_speaking(False)

    mic_frame = InputAudioRawFrame(
        audio=pcm_s16,
        sample_rate=16000,
        num_channels=1,
    )
    await gate.process_frame(mic_frame, FrameDirection.DOWNSTREAM)

    input_frames = [f for f, _ in pushed if isinstance(f, InputAudioRawFrame)]
    assert len(input_frames) == 0
