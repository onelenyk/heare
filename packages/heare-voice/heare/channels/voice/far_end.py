"""The far-end reference: what the speaker is playing, at the mic's rate.

AEC3 is adaptive — it needs an uninterrupted stream at a fixed block size
to converge its filter and to estimate the speaker-to-microphone delay.
So every microphone block is processed, always, in exact 10 ms chunks,
with a far-end reference that advances in lockstep — zeros while nothing
is playing.

This is the buffer half of that, framework-free, so the spine owns it
without importing the old engine's tree. The pipecat frame processors
that feed and consume it (``make_far_end_collector``,
``make_continuous_aec``) stay behind with that engine.
"""

from __future__ import annotations

import logging
from collections import deque

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
FRAME = 160  # 10 ms — AEC3's native block size


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
        # Whether the speaker is currently emitting. Tracked here because
        # this object already sits at the output; the level probes need it
        # to separate "the room was quiet" from "the filter deleted the
        # signal", which sound identical without the split.
        self.bot_speaking: bool = False

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


__all__ = ["FarEnd", "FRAME", "SAMPLE_RATE"]
