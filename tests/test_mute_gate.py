"""Tests for src/mute_gate.py — state-based mute and the audio-dropping
processor placed after TTS in the pipeline."""
from __future__ import annotations

from pathlib import Path

import pytest


class _MockState:
    """Minimal State mock for testing mute gate processors."""
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


def test_is_muted_false_when_flag_missing(tmp_path: Path):
    from src.pipeline.stages.mute_gate import is_muted

    assert is_muted(tmp_path / "mute.flag") is False


def test_is_muted_true_when_flag_exists(tmp_path: Path):
    from src.pipeline.stages.mute_gate import is_muted

    flag = tmp_path / "mute.flag"
    flag.touch()
    assert is_muted(flag) is True


def test_set_mute_creates_and_removes(tmp_path: Path):
    from src.pipeline.stages.mute_gate import is_muted, set_mute

    flag = tmp_path / "nested" / "mute.flag"
    assert set_mute(flag, True) is True
    assert flag.exists()
    assert is_muted(flag) is True
    assert set_mute(flag, False) is False
    assert not flag.exists()


def test_toggle_mute_flips(tmp_path: Path):
    from src.pipeline.stages.mute_gate import toggle_mute

    flag = tmp_path / "mute.flag"
    assert toggle_mute(flag) is True
    assert toggle_mute(flag) is False


@pytest.mark.asyncio
async def test_gate_drops_tts_audio_when_muted(tmp_path: Path):
    from pipecat.frames.frames import TTSAudioRawFrame, TTSStoppedFrame

    from src.pipeline.stages.mute_gate import create_mute_gate

    state = _MockState(mute_bot="1")
    proc = create_mute_gate(state=state)

    captured: list = []

    async def fake_push(frame, direction=None):
        captured.append(frame)

    proc.push_frame = fake_push  # type: ignore[method-assign]

    audio = TTSAudioRawFrame(audio=b"\x00\x00", sample_rate=24000, num_channels=1)
    stopped = TTSStoppedFrame()
    await proc.process_frame(audio, None)
    await proc.process_frame(stopped, None)

    # Audio dropped, control frame passed through.
    assert audio not in captured
    assert stopped in captured


@pytest.mark.asyncio
async def test_gate_drops_tts_audio_in_silent_and_meeting_mode(
    tmp_path: Path,
):
    """silent / meeting must mechanically mute TTS even with no mute flag."""
    from pipecat.frames.frames import TTSAudioRawFrame, TTSStoppedFrame

    from src.pipeline.language_state import LanguageState
    from src.pipeline.session_state import SessionState
    from src.pipeline.stages.mute_gate import create_mute_gate

    state = _MockState()
    ss = SessionState(LanguageState(), initial_mode="silent")
    proc = create_mute_gate(state=state, session_state=ss)

    captured: list = []

    async def fake_push(frame, direction=None):
        captured.append(frame)

    proc.push_frame = fake_push  # type: ignore[method-assign]

    audio = TTSAudioRawFrame(audio=b"\x00\x00", sample_rate=24000, num_channels=1)
    stopped = TTSStoppedFrame()
    await proc.process_frame(audio, None)
    await proc.process_frame(stopped, None)
    assert audio not in captured  # silent → dropped
    assert stopped in captured  # control frame still flows

    # Switch to meeting → still muted.
    ss.set_mode("meeting")
    captured.clear()
    a2 = TTSAudioRawFrame(audio=b"\x00\x00", sample_rate=24000, num_channels=1)
    await proc.process_frame(a2, None)
    assert a2 not in captured

    # Switch to ambient → speech flows again (live, no restart).
    ss.set_mode("ambient")
    captured.clear()
    a3 = TTSAudioRawFrame(audio=b"\x00\x00", sample_rate=24000, num_channels=1)
    await proc.process_frame(a3, None)
    assert captured == [a3]


def test_voice_muted_flags_per_mode():
    from src.agent.modes import MODE_PROFILES

    # Deprecated alias still works.
    assert MODE_PROFILES["silent"].mute_output is True
    assert MODE_PROFILES["meeting"].mute_output is True
    assert MODE_PROFILES["ambient"].mute_output is False
    assert MODE_PROFILES["focus"].mute_output is False
    assert MODE_PROFILES["assistant"].mute_output is False
    # Canonical field.
    assert MODE_PROFILES["silent"].voice_muted is True
    assert MODE_PROFILES["meeting"].voice_muted is True
    assert MODE_PROFILES["ambient"].voice_muted is False
    assert MODE_PROFILES["focus"].voice_muted is False
    assert MODE_PROFILES["assistant"].voice_muted is False


def test_outputs_per_mode():
    from src.agent.modes import MODE_PROFILES

    _ALL = frozenset({"voice", "text", "canvas"})
    assert MODE_PROFILES["ambient"].outputs == _ALL
    assert MODE_PROFILES["focus"].outputs == _ALL
    assert MODE_PROFILES["assistant"].outputs == _ALL
    assert MODE_PROFILES["silent"].outputs == frozenset({"text", "canvas"})
    assert MODE_PROFILES["meeting"].outputs == frozenset({"text"})


@pytest.mark.asyncio
async def test_gate_passes_audio_when_not_muted(tmp_path: Path):
    from pipecat.frames.frames import TTSAudioRawFrame

    from src.pipeline.stages.mute_gate import create_mute_gate

    state = _MockState()
    proc = create_mute_gate(state=state)

    captured: list = []

    async def fake_push(frame, direction=None):
        captured.append(frame)

    proc.push_frame = fake_push  # type: ignore[method-assign]

    audio = TTSAudioRawFrame(audio=b"\x00\x00", sample_rate=24000, num_channels=1)
    await proc.process_frame(audio, None)

    assert captured == [audio]


# ---------------------------------------------------------------------------
# Input (mic) mute
# ---------------------------------------------------------------------------


def test_input_mute_helpers_independent_from_output(tmp_path: Path):
    """Mic and bot mutes are independent flag files."""
    from src.pipeline.stages.mute_gate import (
        is_input_muted,
        is_muted,
        toggle_input_mute,
        toggle_mute,
    )

    bot_flag = tmp_path / "mute.flag"
    mic_flag = tmp_path / "mute_input.flag"

    toggle_mute(bot_flag)
    assert is_muted(bot_flag) is True
    assert is_input_muted(mic_flag) is False  # mic still on

    toggle_input_mute(mic_flag)
    assert is_input_muted(mic_flag) is True
    assert is_muted(bot_flag) is True  # bot still muted


@pytest.mark.asyncio
async def test_input_gate_drops_input_audio_when_muted(tmp_path: Path):
    from pipecat.frames.frames import InputAudioRawFrame, TTSStartedFrame

    from src.pipeline.stages.mute_gate import create_input_mute_gate

    state = _MockState(mute_mic="1")
    proc = create_input_mute_gate(state=state)

    captured: list = []

    async def fake_push(frame, direction=None):
        captured.append(frame)

    proc.push_frame = fake_push  # type: ignore[method-assign]

    audio = InputAudioRawFrame(
        audio=b"\x00\x00", sample_rate=16000, num_channels=1
    )
    started = TTSStartedFrame()
    await proc.process_frame(audio, None)
    await proc.process_frame(started, None)

    assert audio not in captured
    assert started in captured


@pytest.mark.asyncio
async def test_input_gate_passes_audio_when_not_muted(tmp_path: Path):
    from pipecat.frames.frames import InputAudioRawFrame

    from src.pipeline.stages.mute_gate import create_input_mute_gate

    state = _MockState()
    proc = create_input_mute_gate(state=state)

    captured: list = []

    async def fake_push(frame, direction=None):
        captured.append(frame)

    proc.push_frame = fake_push  # type: ignore[method-assign]

    audio = InputAudioRawFrame(
        audio=b"\x01\x02", sample_rate=16000, num_channels=1
    )
    await proc.process_frame(audio, None)
    assert captured == [audio]


@pytest.mark.asyncio
async def test_input_gate_logs_mute_edges_not_every_frame(tmp_path: Path, caplog):
    """The flood we set out to kill: a muted mic must log the mute/unmute
    EDGES, not once per N dropped frames."""
    import logging as _logging

    from pipecat.frames.frames import InputAudioRawFrame

    from src.pipeline.stages.mute_gate import create_input_mute_gate

    state = _MockState(mute_mic="1")
    proc = create_input_mute_gate(state=state)

    async def fake_push(frame, direction=None):
        pass

    proc.push_frame = fake_push  # type: ignore[method-assign]

    def _audio():
        return InputAudioRawFrame(audio=b"\x00\x00", sample_rate=16000, num_channels=1)

    with caplog.at_level(_logging.INFO, logger="heare.mute_gate"):
        # 200 dropped frames while muted...
        for _ in range(200):
            await proc.process_frame(_audio(), None)
        # ...then unmute and let one frame through.
        state._data["mute_mic"] = "0"
        await proc.process_frame(_audio(), None)

    info_lines = [r.getMessage() for r in caplog.records if r.levelno == _logging.INFO]
    # Exactly two INFO lines for the whole session: one mute, one unmute —
    # not ~4 (200 / 50) as the old %-50 counter produced.
    muted = [m for m in info_lines if "mic muted" in m]
    unmuted = [m for m in info_lines if "mic unmuted" in m]
    assert len(muted) == 1, info_lines
    assert len(unmuted) == 1, info_lines
    # The unmute line reports the total dropped so the count isn't lost.
    assert "200" in unmuted[0]


@pytest.mark.asyncio
async def test_input_gate_relogs_after_remute(tmp_path: Path, caplog):
    """A second mute session logs its own edge — the flag resets on unmute."""
    import logging as _logging

    from pipecat.frames.frames import InputAudioRawFrame

    from src.pipeline.stages.mute_gate import create_input_mute_gate

    state = _MockState(mute_mic="1")
    proc = create_input_mute_gate(state=state)

    async def fake_push(frame, direction=None):
        pass

    proc.push_frame = fake_push  # type: ignore[method-assign]

    def _audio():
        return InputAudioRawFrame(audio=b"\x00\x00", sample_rate=16000, num_channels=1)

    with caplog.at_level(_logging.INFO, logger="heare.mute_gate"):
        await proc.process_frame(_audio(), None)          # muted
        state._data["mute_mic"] = "0"
        await proc.process_frame(_audio(), None)          # unmuted
        state._data["mute_mic"] = "1"
        await proc.process_frame(_audio(), None)          # muted again

    muted = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == _logging.INFO and "mic muted" in r.getMessage()
    ]
    assert len(muted) == 2
