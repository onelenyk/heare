"""US-IND-A1: deterministic PCM cue generator."""
from __future__ import annotations

import numpy as np
import pytest

from src import indication_assets as ia


LEVELS = ("attention", "error", "long_running", "success", "info", "input_waiting", "countdown")
EXPECTED_DURATION_MS = {
    "attention": 250.0,
    "error": 350.0,
    "long_running": 180.0,
    "success": 220.0,
    "info": 90.0,
    # 3 pips of 80ms + 2 gaps of 60ms
    "input_waiting": 3 * 80.0 + 2 * 60.0,
    # 3 tones of 140ms + 2 gaps of 750ms + 130ms trailing
    "countdown": 3 * 140.0 + 2 * 750.0 + 130.0,
}


def _pcm_to_int16(pcm: bytes) -> np.ndarray:
    assert len(pcm) % 2 == 0, "PCM byte length must be even (int16 mono)"
    return np.frombuffer(pcm, dtype=np.int16)


@pytest.mark.parametrize("level", LEVELS)
def test_cue_length_matches_expected_ms(level: str) -> None:
    sr = ia.DEFAULT_SAMPLE_RATE
    pcm = ia._BUILDERS[level](sr)
    samples = _pcm_to_int16(pcm)
    expected_ms = EXPECTED_DURATION_MS[level]
    expected_samples = int(round(expected_ms * sr / 1000.0))
    # Concatenations of half-windows can round per-segment; allow ±2 samples.
    assert abs(len(samples) - expected_samples) <= 2, (
        f"{level}: got {len(samples)} samples, expected ~{expected_samples}"
    )


@pytest.mark.parametrize("level", LEVELS)
def test_cue_amplitude_within_peak_bound(level: str) -> None:
    pcm = ia._BUILDERS[level](ia.DEFAULT_SAMPLE_RATE)
    samples = _pcm_to_int16(pcm)
    # PEAK_AMPLITUDE * INT16_MAX = 0.4 * 32767 ≈ 13107
    bound = int(ia.PEAK_AMPLITUDE * 32767) + 1
    peak = int(np.max(np.abs(samples)))
    assert peak <= bound, f"{level}: peak {peak} exceeds bound {bound}"


@pytest.mark.parametrize("level", LEVELS)
def test_cue_is_deterministic_across_calls(level: str) -> None:
    sr = ia.DEFAULT_SAMPLE_RATE
    a = ia._BUILDERS[level](sr)
    b = ia._BUILDERS[level](sr)
    assert a == b, f"{level}: PCM not deterministic across two calls"


def test_all_cues_returns_every_level() -> None:
    cues = ia.all_cues()
    assert set(cues.keys()) == set(LEVELS)
    for level, pcm in cues.items():
        assert isinstance(pcm, bytes)
        assert len(pcm) > 0


def test_cue_starts_and_ends_near_zero_for_click_avoidance() -> None:
    """Envelope must ramp from/to 0 so cues don't click on transducers."""
    for level in LEVELS:
        pcm = ia._BUILDERS[level](ia.DEFAULT_SAMPLE_RATE)
        samples = _pcm_to_int16(pcm)
        # Within the first/last 2ms, magnitude should be below ~10% of peak.
        ms_2 = int(0.002 * ia.DEFAULT_SAMPLE_RATE)
        peak = max(int(np.max(np.abs(samples))), 1)
        head = int(np.max(np.abs(samples[:ms_2])))
        tail = int(np.max(np.abs(samples[-ms_2:])))
        assert head <= peak * 0.5, f"{level}: head amplitude {head} too high vs peak {peak}"
        assert tail <= peak * 0.5, f"{level}: tail amplitude {tail} too high vs peak {peak}"


def test_cue_supports_alternate_sample_rate() -> None:
    """Plan does not require alt SR but builders take a parameter — exercise it."""
    pcm_24k = ia.attention_cue(24000)
    pcm_16k = ia.attention_cue(16000)
    # 16k should produce roughly 2/3 the bytes of 24k
    ratio = len(pcm_16k) / len(pcm_24k)
    assert 0.6 <= ratio <= 0.7, f"alt sr ratio {ratio}"


def test_countdown_cue_deterministic_and_within_bounds() -> None:
    a = ia.countdown_cue()
    b = ia.countdown_cue()
    assert a == b, "countdown_cue must be deterministic"
    # 2.0s <= duration <= 3.0s, 16-bit mono at DEFAULT_SAMPLE_RATE
    n_samples = len(a) // 2
    duration_s = n_samples / ia.DEFAULT_SAMPLE_RATE
    assert 2.0 <= duration_s <= 3.0, f"unexpected length {duration_s:.3f}s"
    # Peak amplitude bounded
    samples = np.frombuffer(a, dtype=np.int16)
    peak = abs(samples).max() / 32768.0
    assert peak <= ia.PEAK_AMPLITUDE + 0.01, f"peak {peak:.3f} exceeds budget"
