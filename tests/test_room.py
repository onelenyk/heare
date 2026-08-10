"""The simulated room's arithmetic.

The scenario runner needs a network and half a minute; these cover the
parts that decide whether its numbers mean anything, and they run in
milliseconds.

A live scenario measured 503 ms to barge-in with 0 self-hearing at
-10 dB echo and 120 ms delay — see docs/findings/measuring.md.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.pipeline.room import FRAME_SAMPLES, SAMPLE_RATE, Room, Say, _overlap


def _speech(n: int = FRAME_SAMPLES, amp: int = 8000) -> np.ndarray:
    t = np.arange(n) / SAMPLE_RATE
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.int16)


def _pcm(samples: np.ndarray) -> bytes:
    return samples.astype(np.int16).tobytes()


def _rms(frame: bytes) -> float:
    x = np.frombuffer(frame, dtype=np.int16).astype(np.float64)
    return float(np.sqrt((x**2).mean()))


# ── the room's echo ───────────────────────────────────────────────────


def test_a_silent_room_returns_only_the_noise_floor():
    room = Room(echo_db=-10, delay_ms=120, noise_dbfs=-60)
    frame = room.mic_frame(np.zeros(FRAME_SAMPLES, dtype=np.int16))
    assert _rms(frame) < 100  # -60 dBFS is about 33 rms


def test_speech_passes_through_untouched_when_nothing_is_playing():
    room = Room(noise_dbfs=-120)
    speech = _speech()
    out = np.frombuffer(room.mic_frame(speech), dtype=np.int16)
    assert np.abs(out.astype(int) - speech.astype(int)).max() <= 2


def test_echo_arrives_only_after_the_room_delay():
    """A canceller fed an echo that has not arrived yet learns nothing."""
    room = Room(echo_db=0, delay_ms=120, noise_dbfs=-120)
    room.played(_pcm(_speech(SAMPLE_RATE, amp=10000)), SAMPLE_RATE)

    silence = np.zeros(FRAME_SAMPLES, dtype=np.int16)
    early = [_rms(room.mic_frame(silence)) for _ in range(5)]  # 100 ms
    later = [_rms(room.mic_frame(silence)) for _ in range(5)]  # 100-200 ms

    assert max(early) < 1.0, "echo cannot precede its own delay"
    assert max(later) > 100, "echo must arrive once the delay has passed"


@pytest.mark.parametrize("echo_db, expected_ratio", [(0, 1.0), (-6, 0.5), (-20, 0.1)])
def test_echo_level_follows_the_setting(echo_db: float, expected_ratio: float):
    room = Room(echo_db=echo_db, delay_ms=0, noise_dbfs=-120)
    loud = _speech(SAMPLE_RATE, amp=10000)
    room.played(_pcm(loud), SAMPLE_RATE)

    frame = room.mic_frame(np.zeros(FRAME_SAMPLES, dtype=np.int16))
    ratio = _rms(frame) / _rms(_pcm(loud[:FRAME_SAMPLES]))
    assert ratio == pytest.approx(expected_ratio, rel=0.1)


def test_playback_is_resampled_to_the_microphone_rate():
    """TTS is 24 kHz, the room hears at 16 kHz."""
    room = Room(echo_db=0, delay_ms=0, noise_dbfs=-120)
    room.played(np.zeros(2400, dtype=np.int16).tobytes(), 24_000)
    assert room._played.size == pytest.approx(1600, abs=2)


def test_speech_and_echo_add_rather_than_replace():
    """Double-talk is the case the whole exercise is about."""
    room = Room(echo_db=0, delay_ms=0, noise_dbfs=-120)
    tone = _speech(SAMPLE_RATE, amp=4000)
    room.played(_pcm(tone), SAMPLE_RATE)

    speech = _speech(amp=4000)
    mixed = np.frombuffer(room.mic_frame(speech), dtype=np.int16)

    assert _rms(mixed.tobytes()) > _rms(_pcm(speech)) * 1.5


def test_frames_are_always_one_block_long():
    """A short clip must not shorten the frame — the clock would drift."""
    room = Room()
    assert len(room.mic_frame(_speech(50))) == FRAME_SAMPLES * 2
    assert len(room.mic_frame(np.zeros(0, dtype=np.int16))) == FRAME_SAMPLES * 2


# ── self-hearing detection ────────────────────────────────────────────


def test_overlap_recognises_the_assistant_quoting_itself():
    assert _overlap("Меркурій найближчий до Сонця", "меркурій найближчий сонця") > 0.5


def test_overlap_ignores_an_unrelated_reply():
    assert _overlap("Стоп, зачекай", "Меркурій найближчий до Сонця") < 0.2


def test_overlap_is_safe_on_empty_text():
    assert _overlap("", "щось") == 0.0


# ── the script ────────────────────────────────────────────────────────


def test_interruptions_are_scheduled_against_speech_not_the_clock():
    """Cutting in at a repeatable moment is the reason this exists — a
    person in a room cannot do it twice the same way."""
    line = Say(at="mid_speech", text="Стоп")
    assert line.at == "mid_speech"
    assert line.delay_after_bot_starts < 0.6, (
        "the reply rules cap most answers at one sentence, so a later cut "
        "would land after the assistant already stopped"
    )
