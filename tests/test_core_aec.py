"""Regression tests for the echo canceller.

Each test here corresponds to a bug that shipped and survived for months,
because the only instrument was a pair of ears and every one of these
failures sounds exactly like "echo cancellation is working".

See docs/findings/echo-cancellation.md.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.core.aec import FRAME, SAMPLE_RATE, DelayEstimator, FarEnd


def _tone(n: int, freq: float = 300.0, amp: float = 3000.0, phase: int = 0) -> np.ndarray:
    t = (np.arange(n) + phase) / SAMPLE_RATE
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


# ── FarEnd ────────────────────────────────────────────────────────────


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


def test_collector_tracks_whether_the_speaker_is_emitting():
    """The level probes split their readings on this flag. Without the
    split, "the room was quiet" and "the filter deleted the signal" look
    identical — which is how the mute hid for months."""
    import asyncio

    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        TTSAudioRawFrame,
    )

    from src.core.aec import make_far_end_collector

    far = FarEnd()
    collector = make_far_end_collector(far)

    async def noop(frame, direction):
        return None

    collector.push_frame = noop  # type: ignore[method-assign]

    async def drive():
        assert far.bot_speaking is False
        await collector.process_frame(BotStartedSpeakingFrame(), None)
        assert far.bot_speaking is True
        await collector.process_frame(
            TTSAudioRawFrame(
                audio=np.zeros(FRAME, dtype=np.int16).tobytes(),
                sample_rate=SAMPLE_RATE,
                num_channels=1,
            ),
            None,
        )
        assert far.pending == FRAME
        await collector.process_frame(BotStoppedSpeakingFrame(), None)
        assert far.bot_speaking is False

    asyncio.run(drive())


# ── the scale bug ─────────────────────────────────────────────────────


def test_audio_processor_expects_unit_scale():
    """The bug that made 'echo cancellation' a microphone mute.

    ``AudioProcessor.process`` takes floats in [-1, 1] and clips beyond.
    Handed int16-scaled values it returns a signal clipped to ±1.0 —
    amplitude 1 out of 32768, about −90 dB. Silence, on every sample,
    for as long as the bot was speaking.
    """
    AudioProcessor = pytest.importorskip("pywebrtc_audio").AudioProcessor

    def peak_out(scale: float) -> float:
        apm = AudioProcessor(
            sample_rate=SAMPLE_RATE,
            num_channels=1,
            echo_cancellation=True,
            noise_suppression=False,
            auto_gain_control=False,
            stream_delay_ms=30,
        )
        silence = np.zeros(FRAME, dtype=np.float32)
        out = np.zeros(FRAME, dtype=np.float32)
        for i in range(30):  # let the filter settle
            block = _tone(FRAME, phase=i * FRAME) / scale
            out = np.asarray(apm.process(block, silence), dtype=np.float32)
        return float(np.abs(out).max()) * scale

    unit_scale = peak_out(32768.0)
    int16_scale = peak_out(1.0)

    assert unit_scale > 1000, "near-end speech must survive at unit scale"
    assert int16_scale < 10, "int16 scale is the bug: everything clipped away"


@pytest.mark.parametrize("noise_suppression", [False, True])
def test_continuous_aec_passes_speech_through_when_nothing_is_playing(
    noise_suppression: bool,
):
    """With no far-end signal there is no echo, so the voice must survive.

    The old filter only ran while the bot spoke, which hid this: it was
    never asked to pass audio through untouched.
    """
    pytest.importorskip("pywebrtc_audio")
    from pipecat.frames.frames import InputAudioRawFrame

    from src.core.aec import make_continuous_aec

    aec = make_continuous_aec(
        FarEnd(), stream_delay_ms=120, noise_suppression=noise_suppression
    )

    pushed: list = []

    async def capture(frame, direction):
        pushed.append(frame)

    aec.push_frame = capture  # type: ignore[method-assign]

    async def feed():
        peak = 0.0
        for i in range(40):
            audio = _tone(FRAME * 2, phase=i * FRAME * 2).astype(np.int16)
            frame = InputAudioRawFrame(
                audio=audio.tobytes(), sample_rate=SAMPLE_RATE, num_channels=1
            )
            await aec.process_frame(frame, None)
            out = np.frombuffer(pushed[-1].audio, dtype=np.int16)
            peak = max(peak, float(np.abs(out).max()))
        return peak

    import asyncio

    peak = asyncio.run(feed())
    assert peak > 500, f"speech was destroyed on the way through (peak {peak})"


def test_continuous_aec_preserves_frame_length():
    """VAD timing depends on frames keeping their size."""
    pytest.importorskip("pywebrtc_audio")
    import asyncio

    from pipecat.frames.frames import InputAudioRawFrame

    from src.core.aec import make_continuous_aec

    aec = make_continuous_aec(FarEnd(), stream_delay_ms=120)
    seen: list[int] = []

    async def capture(frame, direction):
        seen.append(len(frame.audio))

    aec.push_frame = capture  # type: ignore[method-assign]

    async def feed():
        for size in (320, 320, 480, 160, 1024):
            frame = InputAudioRawFrame(
                audio=np.zeros(size, dtype=np.int16).tobytes(),
                sample_rate=SAMPLE_RATE,
                num_channels=1,
            )
            await aec.process_frame(frame, None)

    asyncio.run(feed())
    assert seen == [640, 640, 960, 320, 2048]


# ── delay estimation ──────────────────────────────────────────────────


def test_delay_estimator_recovers_a_known_lag():
    """The number that was guessed at 30 ms and measured at ~125."""
    lag_samples = int(0.075 * SAMPLE_RATE)  # 75 ms
    est = DelayEstimator(window_s=1.0, max_delay_ms=400)

    rng = np.random.default_rng(7)
    far_signal = (rng.standard_normal(SAMPLE_RATE + lag_samples) * 3000).astype(
        np.float32
    )
    near_signal = np.concatenate(
        [np.zeros(lag_samples, dtype=np.float32), far_signal[:SAMPLE_RATE]]
    )

    for i in range(0, SAMPLE_RATE, FRAME):
        est.add(near_signal[i : i + FRAME], far_signal[i : i + FRAME])

    assert est.ready()
    found = est.estimate()
    assert found is not None
    measured, confidence = found
    assert measured == pytest.approx(75.0, abs=10.0)
    assert confidence > 0.2


def test_delay_estimator_stays_quiet_when_nothing_is_playing():
    """No far-end energy means no measurement, not a spurious one."""
    est = DelayEstimator(window_s=0.5, max_delay_ms=200)
    for _ in range(int(0.5 * SAMPLE_RATE) // FRAME):
        est.add(_tone(FRAME), np.zeros(FRAME, dtype=np.float32))
    assert est.ready()
    assert est.estimate() is None


@pytest.mark.parametrize("echo_db", [-10.0, -20.0])
def test_a_voice_over_the_echo_survives_the_canceller(echo_db: float):
    """Double talk: the person speaks while the assistant is speaking.
    This is the whole of barge-in.

    The near-end here is bursts of broadband noise, not a tone. That is
    not a detail: given a steady tone the canceller removes it almost
    entirely — 2000 in, 29 out — because a stationary signal inside an
    echo-dominated band is exactly what its residual suppressor exists to
    remove. A first version of this test used a tone, measured 36 dB of
    attenuation, and would have had me report that the canceller eats
    interruptions. It does not. Speech is not stationary.
    """
    pytest.importorskip("pywebrtc_audio")
    import asyncio

    from pipecat.frames.frames import InputAudioRawFrame

    from src.core.aec import make_continuous_aec

    far = FarEnd()
    far.bot_speaking = True
    aec = make_continuous_aec(far, stream_delay_ms=0, noise_suppression=True)

    pushed: list = []

    async def capture(frame, direction):
        pushed.append(frame)

    aec.push_frame = capture  # type: ignore[method-assign]

    rng = np.random.default_rng(7)
    gain = 10 ** (echo_db / 20)

    async def feed() -> float:
        voice_peak = 0.0
        for i in range(80):
            n = FRAME * 2
            bot = _tone(n, freq=300.0, amp=8000.0, phase=i * n)
            far.push(bot.astype(np.int16).tobytes(), SAMPLE_RATE)
            # Speaking in bursts, at a quarter of the assistant's level.
            speaking = (i // 10) % 2 == 0
            near = 2000.0 * rng.standard_normal(n) if speaking else np.zeros(n)
            mic = (near + bot * gain).astype(np.int16)
            await aec.process_frame(
                InputAudioRawFrame(
                    audio=mic.tobytes(), sample_rate=SAMPLE_RATE, num_channels=1
                ),
                None,
            )
            if i > 30 and speaking:
                out = np.frombuffer(pushed[-1].audio, dtype=np.int16)
                voice_peak = max(voice_peak, float(np.abs(out).max()))
        return voice_peak

    peak = asyncio.run(feed())

    # Voice activity detection needs a level, not a waveform. Below this
    # an interruption is inaudible to it and the assistant talks over you.
    assert peak > 500, (
        f"the interrupting voice came out at {peak:.0f} against an echo at "
        f"{echo_db} dB — barge-in cannot fire on that"
    )
