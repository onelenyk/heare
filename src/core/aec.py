"""Echo cancellation that runs all the time.

The stage this replaces called ``process()`` only while the bot was
speaking, on whatever frame length happened to arrive, with a reference
taken from a ring buffer. Measured at the microphone that produced
−120 dB against −29 dB raw: not cancellation, a mute. AEC3 is adaptive —
it needs an uninterrupted stream at a fixed frame size to converge its
filter and to estimate the speaker-to-microphone delay. Restarted every
utterance, it never gets there and falls back to suppressing everything.

So: every microphone frame is processed, always, in exact 10 ms chunks,
with a far-end reference that advances in lockstep — zeros while nothing
is playing. The delay between handing audio to the output transport and
hearing it in the room is left for AEC3's own estimator, which is what
it is for.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
FRAME = 160  # 10 ms — AEC3's native block size


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) if x.size else 0.0


class FarEnd:
    """Bot output at 16 kHz, drained one mic frame at a time.

    TTS arrives in bursts, the speaker plays in real time, and the mic
    delivers in real time. Draining this queue at the mic's rate keeps
    the reference advancing with the room instead of with the network.
    """

    def __init__(self, max_seconds: float = 60.0) -> None:
        # No small cap: a deque that overflows drops from the left, and
        # the left is exactly what is about to be played. Silently losing
        # those samples desynchronises the reference for the rest of the
        # utterance.
        self._q: deque[float] = deque(maxlen=int(max_seconds * SAMPLE_RATE))

    def push(self, pcm_s16le: bytes, source_rate: int) -> None:
        samples = np.frombuffer(pcm_s16le, dtype=np.int16).astype(np.float32)
        if samples.size == 0:
            return
        if source_rate != SAMPLE_RATE:
            new_len = max(1, int(samples.size * SAMPLE_RATE / source_rate))
            samples = np.interp(
                np.linspace(0, samples.size - 1, new_len),
                np.arange(samples.size),
                samples,
            )
        self._q.extend(samples.tolist())

    def take(self, n: int) -> np.ndarray:
        """Next ``n`` reference samples; zeros once playback has drained."""
        out = np.zeros(n, dtype=np.float32)
        for i in range(min(n, len(self._q))):
            out[i] = self._q.popleft()
        return out

    def clear(self) -> None:
        self._q.clear()

    @property
    def pending(self) -> int:
        return len(self._q)


def make_far_end_collector(far: FarEnd) -> Any:
    """Sits right before the output transport and taps what will be played."""
    from pipecat.frames.frames import (
        InterruptionFrame,
        TTSAudioRawFrame,
    )
    from pipecat.processors.frame_processor import FrameProcessor

    class FarEndCollector(FrameProcessor):
        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)

            if isinstance(frame, TTSAudioRawFrame):
                far.push(frame.audio, getattr(frame, "sample_rate", 24000))
            elif isinstance(frame, InterruptionFrame):
                # Cancelled audio is never played, so it must not stay in
                # the reference — otherwise AEC spends the next seconds
                # subtracting an echo that no speaker ever produced.
                far.clear()

            await self.push_frame(frame, direction)

    return FarEndCollector()


class DelayEstimator:
    """Measures the real speaker-to-microphone delay, instead of guessing.

    ``stream_delay_ms`` tells AEC3 how long after we hand audio to the
    output transport it comes back through the room. Guess it wrong and
    the adaptive filter aligns against the wrong samples, which caps how
    much echo it can remove. Cross-correlating what we sent against what
    the microphone heard gives the number directly.
    """

    def __init__(self, window_s: float = 2.0, max_delay_ms: int = 400) -> None:
        self._n = int(window_s * SAMPLE_RATE)
        self._max_lag = int(max_delay_ms * SAMPLE_RATE / 1000)
        self._near: deque[float] = deque(maxlen=self._n)
        self._far: deque[float] = deque(maxlen=self._n)

    def add(self, near: np.ndarray, far: np.ndarray) -> None:
        self._near.extend(near.tolist())
        self._far.extend(far.tolist())

    def ready(self) -> bool:
        return len(self._near) >= self._n

    def estimate(self) -> tuple[float, float] | None:
        """Return ``(delay_ms, confidence)`` or None if nothing was playing."""
        near = np.asarray(self._near, dtype=np.float32)
        far = np.asarray(self._far, dtype=np.float32)
        if far.size < self._n or float(np.sqrt(np.mean(far**2))) < 20.0:
            return None

        near = near - near.mean()
        far = far - far.mean()
        size = 1 << int(np.ceil(np.log2(near.size + self._max_lag)))
        corr = np.fft.irfft(
            np.fft.rfft(near, size) * np.conj(np.fft.rfft(far, size)), size
        )[: self._max_lag]
        denom = float(np.linalg.norm(near) * np.linalg.norm(far)) or 1.0
        lag = int(np.argmax(corr))
        return lag * 1000.0 / SAMPLE_RATE, float(corr[lag] / denom)

    def clear(self) -> None:
        self._near.clear()
        self._far.clear()


def make_continuous_aec(
    far: FarEnd,
    *,
    stream_delay_ms: int = 30,
    noise_suppression: bool = True,
    measure_delay: bool = False,
) -> Any:
    from pipecat.frames.frames import InputAudioRawFrame
    from pipecat.processors.frame_processor import FrameProcessor
    from pywebrtc_audio import AudioProcessor

    class ContinuousAEC(FrameProcessor):
        def __init__(self) -> None:
            super().__init__()
            self._apm = AudioProcessor(
                sample_rate=SAMPLE_RATE,
                num_channels=1,
                echo_cancellation=True,
                noise_suppression=noise_suppression,
                auto_gain_control=False,
                stream_delay_ms=stream_delay_ms,
            )
            self._near = np.zeros(0, dtype=np.float32)
            self._clean = np.zeros(0, dtype=np.float32)
            self._warned = False
            self._delay = DelayEstimator() if measure_delay else None
            self._suppression: list[float] = []

        def _report(self) -> None:
            assert self._delay is not None
            found = self._delay.estimate()
            self._delay.clear()
            if found is None:  # nothing was playing in this window
                self._suppression.clear()
                return
            measured, confidence = found
            mean = (
                sum(self._suppression) / len(self._suppression)
                if self._suppression
                else 0.0
            )
            self._suppression.clear()
            logger.info(
                "aec: delay measured %3.0f ms (conf %.2f) vs configured %d ms  "
                "— suppression %.1f dB, reference queue %d ms",
                measured,
                confidence,
                stream_delay_ms,
                mean,
                far.pending * 1000 // SAMPLE_RATE,
            )

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)

            if isinstance(frame, InputAudioRawFrame) and frame.audio:
                wanted = len(frame.audio) // 2
                mic = np.frombuffer(frame.audio, dtype=np.int16).astype(np.float32)
                self._near = np.concatenate([self._near, mic])

                while self._near.size >= FRAME:
                    block, self._near = self._near[:FRAME], self._near[FRAME:]
                    reference = far.take(FRAME)

                    # AudioProcessor works in [-1, 1] and clips anything
                    # past it. Handed int16-scaled values (±32768) it
                    # returns a signal clipped to ±1.0 — amplitude 1 out
                    # of 32768, i.e. −90 dB. That is what made the old
                    # filter look like a microphone mute: it was not
                    # cancelling, it was destroying the signal.
                    out = np.asarray(
                        self._apm.process(block / 32768.0, reference / 32768.0),
                        dtype=np.float32,
                    ) * 32768.0
                    self._clean = np.concatenate([self._clean, out])

                    if self._delay is not None:
                        self._delay.add(block, reference)
                        if _rms(reference) > 20.0:
                            self._suppression.append(
                                20 * np.log10(_rms(block) / max(_rms(out), 1e-6))
                            )
                        if self._delay.ready():
                            self._report()

                if self._clean.size >= wanted:
                    take, self._clean = self._clean[:wanted], self._clean[wanted:]
                else:
                    # Only at start-up, when the residual has not filled a
                    # block yet. A few ms of silence, once.
                    if not self._warned:
                        logger.info("aec: priming (%d/%d)", self._clean.size, wanted)
                        self._warned = True
                    take = np.zeros(wanted, dtype=np.float32)

                frame.audio = np.clip(take, -32768, 32767).astype(np.int16).tobytes()

            await self.push_frame(frame, direction)

    return ContinuousAEC()
