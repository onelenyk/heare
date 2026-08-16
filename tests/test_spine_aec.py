"""Tests for src/spine/aec.py — headless, no audio hardware.

The real canceller (pywebrtc-audio / WebRTC AEC3) is an optional native
dependency. Tests that need it are skipped when it is not importable;
tests that check the fail-open contract force that path deterministically
by making `import pywebrtc_audio` raise, rather than relying on the
environment.
"""

from __future__ import annotations

import importlib.util
import sys

import numpy as np
import pytest

from src.spine.aec import SpineAEC
from src.spine.far_end import FRAME, SAMPLE_RATE, FarEnd

HAVE_WEBRTC = importlib.util.find_spec("pywebrtc_audio") is not None

MIC_RATE = 16000
FAR_RATE = 24000
FRAME_MS = 20
FRAME_SAMPLES = MIC_RATE * FRAME_MS // 1000  # 320
FRAME_BYTES = FRAME_SAMPLES * 2  # 640


def _sine(rate: int, seconds: float, freq: float, amplitude: float) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.int16)


def _rms(samples: np.ndarray) -> float:
    return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2))) if samples.size else 0.0


def _frames(pcm: bytes, frame_bytes: int):
    for i in range(0, len(pcm) - frame_bytes + 1, frame_bytes):
        yield pcm[i : i + frame_bytes]


# --- 1. passthrough when inactive -------------------------------------


def test_inactive_when_library_missing_is_passthrough(monkeypatch):
    # Force `from pywebrtc_audio import AudioProcessor` to raise inside
    # SpineAEC.__init__, regardless of whether the real library is
    # installed in this environment.
    monkeypatch.setitem(sys.modules, "pywebrtc_audio", None)

    aec = SpineAEC()
    assert aec.active is False

    frame = _sine(MIC_RATE, FRAME_MS / 1000, 300.0, 3000.0).tobytes()
    aec.push_far(_sine(FAR_RATE, FRAME_MS / 1000, 300.0, 3000.0).tobytes())
    out = aec.process(frame)

    assert out == frame


# --- 2. real canceller converges and suppresses echo -------------------


@pytest.mark.skipif(not HAVE_WEBRTC, reason="pywebrtc_audio not installed")
def test_real_canceller_suppresses_simulated_echo():
    aec = SpineAEC()
    assert aec.active is True

    duration = 2.0
    freq = 300.0
    amplitude = 3000.0

    far_pcm = _sine(FAR_RATE, duration, freq, amplitude).tobytes()
    mic = _sine(MIC_RATE, duration, freq, amplitude)

    # Feed the whole far-end reference up front, the way a burst of TTS
    # audio would arrive relative to the mic's steady 20 ms cadence.
    aec.push_far(far_pcm)

    out_chunks = []
    for frame in _frames(mic.tobytes(), FRAME_BYTES):
        out_chunks.append(aec.process(frame))
    out = np.frombuffer(b"".join(out_chunks), dtype=np.int16)

    tail_samples = int(0.5 * MIC_RATE)
    in_tail = mic[-tail_samples:]
    out_tail = out[-tail_samples:]

    rms_in = _rms(in_tail)
    rms_out = _rms(out_tail)
    assert rms_in > 0

    suppression_db = 20 * np.log10(rms_in / max(rms_out, 1e-6))
    # Generous convergence budget: the doc'd steady-state is 40-50 dB,
    # this only asserts real suppression happened.
    assert suppression_db > 10.0, (
        f"expected >10 dB suppression after convergence, got {suppression_db:.1f} dB "
        f"(rms_in={rms_in:.1f}, rms_out={rms_out:.1f})"
    )


# --- 3. no far-end audio -> essentially unchanged -----------------------


@pytest.mark.skipif(not HAVE_WEBRTC, reason="pywebrtc_audio not installed")
def test_no_far_end_leaves_mic_essentially_unchanged():
    aec = SpineAEC()
    assert aec.active is True

    mic = _sine(MIC_RATE, 0.5, 300.0, 3000.0)
    # No push_far calls at all — reference is all zeros throughout. Feed
    # enough frames to run past AEC3's brief startup transient (its first
    # block or two are primed near-silent), then judge on the tail.
    out_chunks = [aec.process(f) for f in _frames(mic.tobytes(), FRAME_BYTES)]
    out = np.frombuffer(b"".join(out_chunks), dtype=np.int16)

    tail = int(0.2 * MIC_RATE)
    rms_in = _rms(mic[-tail:])
    rms_out = _rms(out[-tail:])
    assert rms_in > 0
    # Loose bound: with nothing to cancel, steady-state output should be
    # close to the input.
    assert abs(rms_out - rms_in) / rms_in < 0.25


# --- 4. output length always equals input length ------------------------


@pytest.mark.skipif(not HAVE_WEBRTC, reason="pywebrtc_audio not installed")
@pytest.mark.parametrize(
    "n_bytes",
    [FRAME_BYTES, FRAME_BYTES // 2, FRAME_BYTES * 3, 642, 2, 0],
)
def test_process_output_length_matches_input(n_bytes):
    aec = SpineAEC()
    frame = np.zeros(n_bytes // 2, dtype=np.int16).tobytes()
    # Pad to n_bytes if n_bytes was odd-ish; keep exact requested length.
    frame = frame + b"\x00" * (n_bytes - len(frame))

    out = aec.process(frame)
    assert len(out) == len(frame)


def test_process_output_length_matches_input_when_inactive(monkeypatch):
    monkeypatch.setitem(sys.modules, "pywebrtc_audio", None)
    aec = SpineAEC()
    assert aec.active is False

    frame = _sine(MIC_RATE, FRAME_MS / 1000, 300.0, 3000.0).tobytes()
    out = aec.process(frame)
    assert len(out) == len(frame)


# --- 5. malformed/short far chunks don't crash --------------------------


@pytest.mark.skipif(not HAVE_WEBRTC, reason="pywebrtc_audio not installed")
def test_malformed_far_chunks_do_not_crash():
    aec = SpineAEC()

    # Empty chunk.
    aec.push_far(b"")
    # Odd-length chunk (not a whole number of int16 samples).
    aec.push_far(b"\x01")
    aec.push_far(b"\x01\x02\x03")
    # A single valid sample.
    aec.push_far(b"\x10\x20")
    # A large, well-formed chunk after the malformed ones.
    aec.push_far(_sine(FAR_RATE, 0.1, 300.0, 1000.0).tobytes())

    # The canceller must still be usable afterwards.
    frame = _sine(MIC_RATE, FRAME_MS / 1000, 300.0, 3000.0).tobytes()
    out = aec.process(frame)
    assert len(out) == len(frame)


def test_malformed_far_chunks_do_not_crash_when_inactive(monkeypatch):
    monkeypatch.setitem(sys.modules, "pywebrtc_audio", None)
    aec = SpineAEC()

    aec.push_far(b"")
    aec.push_far(b"\x01")
    aec.push_far(b"\x01\x02\x03")

    frame = _sine(MIC_RATE, FRAME_MS / 1000, 300.0, 3000.0).tobytes()
    out = aec.process(frame)
    assert out == frame


# --- 6. the far-end reference buffer ------------------------------------
#
# Copied from tests/test_core_aec.py, which covers the same class where it
# used to live (src/core/aec.py). Duplicated on purpose: the spine's copy
# in src/spine/far_end.py has to stay covered once the old tree goes. Each
# of these corresponds to a bug that shipped and survived for months,
# because the only instrument was a pair of ears and every one of these
# failures sounds exactly like "echo cancellation is working". See
# docs/findings/echo-cancellation.md.


def test_take_returns_zeros_when_nothing_is_playing():
    """AEC3 needs a reference on every block, silence included."""
    far = FarEnd()
    out = far.take(FRAME)
    assert out.shape == (FRAME,)
    assert not out.any()


def test_take_drains_in_order():
    far = FarEnd()
    far.push(np.arange(FRAME * 2, dtype=np.int16).tobytes(), SAMPLE_RATE)

    first = far.take(FRAME)
    second = far.take(FRAME)

    assert first[0] == 0 and first[-1] == FRAME - 1
    assert second[0] == FRAME and second[-1] == FRAME * 2 - 1
    assert far.pending == 0


def test_partial_take_pads_with_silence_rather_than_shifting():
    """A short queue must not slide the reference forward in time."""
    far = FarEnd()
    far.push(np.full(50, 1000, dtype=np.int16).tobytes(), SAMPLE_RATE)

    out = far.take(FRAME)

    assert out.shape == (FRAME,)
    assert (out[:50] == 1000).all()
    assert not out[50:].any()


def test_push_resamples_to_the_microphone_rate():
    """TTS is 24 kHz, the mic is 16 kHz; the reference lives at mic rate."""
    far = FarEnd()
    far.push(np.zeros(2400, dtype=np.int16).tobytes(), 24_000)
    assert far.pending == pytest.approx(1600, abs=2)


def test_a_long_utterance_is_not_silently_truncated():
    """The queue used to cap at 5 s and drop from the left — dropping
    exactly the samples about to be played, which desynchronised the
    reference for the rest of the utterance."""
    far = FarEnd()
    thirty_seconds = np.zeros(30 * SAMPLE_RATE, dtype=np.int16)
    far.push(thirty_seconds.tobytes(), SAMPLE_RATE)
    assert far.pending == 30 * SAMPLE_RATE


def test_clear_drops_cancelled_audio():
    """Interrupted speech is never played, so it must leave the reference."""
    far = FarEnd()
    far.push(np.zeros(FRAME, dtype=np.int16).tobytes(), SAMPLE_RATE)
    far.clear()
    assert far.pending == 0


def test_spine_aec_clear_empties_the_reference():
    """SpineAEC.clear() is the spine's InterruptionFrame: audio that was
    cancelled must not stay in the reference, or AEC3 spends the next
    seconds subtracting an echo no speaker ever produced."""
    aec = SpineAEC()
    aec.push_far(_sine(FAR_RATE, 0.2, 300.0, 3000.0).tobytes())
    assert aec._far.pending > 0
    aec.clear()
    assert aec._far.pending == 0
