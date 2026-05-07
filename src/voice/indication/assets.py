"""Numpy-only PCM asset generator for non-speech indication cues.

Each function produces deterministic int16 mono PCM at the daemon's TTS sample
rate (24 kHz by default). Cues are short (80-500 ms), peak-limited to 0.4 of
full scale, and shaped with a small attack/release envelope so they do not
click on the user's hardware.

No I/O. No randomness. Same byte string across runs (so tests can hash-compare
or assert deterministic length).
"""
from __future__ import annotations

import numpy as np

DEFAULT_SAMPLE_RATE = 24000
PEAK_AMPLITUDE = 0.4
_INT16_MAX = 32767


def _envelope(n_samples: int, attack_ms: float, release_ms: float, sample_rate: int) -> np.ndarray:
    env = np.ones(n_samples, dtype=np.float32)
    a = max(1, int(attack_ms * sample_rate / 1000.0))
    r = max(1, int(release_ms * sample_rate / 1000.0))
    a = min(a, n_samples)
    r = min(r, n_samples - a)
    if a > 0:
        env[:a] = np.linspace(0.0, 1.0, a, dtype=np.float32)
    if r > 0:
        env[-r:] = np.linspace(1.0, 0.0, r, dtype=np.float32)
    return env


def _sine(freq_hz: float, duration_ms: float, sample_rate: int) -> np.ndarray:
    n = int(round(duration_ms * sample_rate / 1000.0))
    t = np.arange(n, dtype=np.float32) / sample_rate
    return np.sin(2.0 * np.pi * freq_hz * t).astype(np.float32)


def _to_pcm16(wave: np.ndarray, peak: float = PEAK_AMPLITUDE) -> bytes:
    clipped = np.clip(wave, -1.0, 1.0) * peak
    return (clipped * _INT16_MAX).astype(np.int16).tobytes()


def attention_cue(sample_rate: int = DEFAULT_SAMPLE_RATE) -> bytes:
    """880 Hz → 1318 Hz two-tone, 250 ms total."""
    half = 125.0
    a = _sine(880.0, half, sample_rate) * _envelope(int(round(half * sample_rate / 1000.0)), 8, 8, sample_rate)
    b = _sine(1318.0, half, sample_rate) * _envelope(int(round(half * sample_rate / 1000.0)), 8, 8, sample_rate)
    return _to_pcm16(np.concatenate([a, b]))


def error_cue(sample_rate: int = DEFAULT_SAMPLE_RATE) -> bytes:
    """220 Hz → 165 Hz descending two-tone, 350 ms total, slight detune."""
    half = 175.0
    n = int(round(half * sample_rate / 1000.0))
    env = _envelope(n, 10, 20, sample_rate)
    a = (_sine(220.0, half, sample_rate) + 0.15 * _sine(223.0, half, sample_rate)) / 1.15
    b = (_sine(165.0, half, sample_rate) + 0.15 * _sine(168.0, half, sample_rate)) / 1.15
    return _to_pcm16(np.concatenate([a * env, b * env]))


def long_running_cue(sample_rate: int = DEFAULT_SAMPLE_RATE) -> bytes:
    """660 Hz single sine, 180 ms, soft envelope."""
    n = int(round(180.0 * sample_rate / 1000.0))
    env = _envelope(n, 25, 50, sample_rate)
    wave = _sine(660.0, 180.0, sample_rate) * env
    return _to_pcm16(wave)


def success_cue(sample_rate: int = DEFAULT_SAMPLE_RATE) -> bytes:
    """523 Hz → 784 Hz major-third leap, 220 ms total."""
    half = 110.0
    n = int(round(half * sample_rate / 1000.0))
    env = _envelope(n, 8, 12, sample_rate)
    a = _sine(523.0, half, sample_rate) * env
    b = _sine(784.0, half, sample_rate) * env
    return _to_pcm16(np.concatenate([a, b]))


def info_cue(sample_rate: int = DEFAULT_SAMPLE_RATE) -> bytes:
    """1046 Hz click, 90 ms, low amplitude."""
    n = int(round(90.0 * sample_rate / 1000.0))
    env = _envelope(n, 5, 30, sample_rate)
    wave = _sine(1046.0, 90.0, sample_rate) * env
    return _to_pcm16(wave, peak=0.25)


def input_waiting_cue(sample_rate: int = DEFAULT_SAMPLE_RATE) -> bytes:
    """880 Hz triple pip, 80 ms × 3 with 60 ms gaps."""
    pip_ms = 80.0
    gap_ms = 60.0
    n_pip = int(round(pip_ms * sample_rate / 1000.0))
    n_gap = int(round(gap_ms * sample_rate / 1000.0))
    env = _envelope(n_pip, 6, 10, sample_rate)
    pip = _sine(880.0, pip_ms, sample_rate) * env
    gap = np.zeros(n_gap, dtype=np.float32)
    chain = np.concatenate([pip, gap, pip, gap, pip])
    return _to_pcm16(chain)


def countdown_cue(sample_rate: int = DEFAULT_SAMPLE_RATE) -> bytes:
    """880 Hz → 660 Hz → 440 Hz descending 3-2-1 countdown, ~1.98s total."""
    tone_ms = 140.0
    gap_ms = 750.0
    n_tone = int(round(tone_ms * sample_rate / 1000.0))
    n_gap = int(round(gap_ms * sample_rate / 1000.0))
    env = _envelope(n_tone, attack_ms=5, release_ms=5, sample_rate=sample_rate)
    tone3 = _sine(880.0, tone_ms, sample_rate) * env
    tone2 = _sine(660.0, tone_ms, sample_rate) * env
    tone1 = _sine(440.0, tone_ms, sample_rate) * env
    gap = np.zeros(n_gap, dtype=np.float32)
    trailing = np.zeros(int(round(130.0 * sample_rate / 1000.0)), dtype=np.float32)
    chain = np.concatenate([tone3, gap, tone2, gap, tone1, trailing])
    return _to_pcm16(chain)


_BUILDERS = {
    "attention": attention_cue,
    "error": error_cue,
    "long_running": long_running_cue,
    "success": success_cue,
    "info": info_cue,
    "input_waiting": input_waiting_cue,
    "countdown": countdown_cue,
}


def all_cues(sample_rate: int = DEFAULT_SAMPLE_RATE) -> dict[str, bytes]:
    """Build PCM for every level. Called once at daemon startup."""
    return {level: builder(sample_rate) for level, builder in _BUILDERS.items()}


__all__ = [
    "DEFAULT_SAMPLE_RATE",
    "PEAK_AMPLITUDE",
    "attention_cue",
    "error_cue",
    "long_running_cue",
    "success_cue",
    "info_cue",
    "input_waiting_cue",
    "countdown_cue",
    "all_cues",
]
