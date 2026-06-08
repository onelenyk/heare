"""Shared echo state for acoustic echo cancellation gating.

The ``EchoState`` object is the single source of truth for the bot's
recent output audio. It is written by ``BotAudioCollector`` (which sits
downstream of TTS and sees ``TTSAudioRawFrame``) and read by
``MicEchoGate`` (which sits upstream of STT and sees
``InputAudioRawFrame``).

The ring buffer stores the last N seconds of bot audio at the mic's
sample rate (16 kHz) so the gate can cross-correlate incoming mic audio
against it. When the correlation is high, the mic is hearing the bot's
own echo and the audio should be suppressed before it reaches STT.

Design notes mirror ``BotSpeechState``: plain-Python, non-async, no
Pipecat imports, single asyncio loop serialises producer/consumer.
"""

from __future__ import annotations

import logging
import time

import numpy as np


logger = logging.getLogger("heare.echo_state")


class EchoState:
    """Ring buffer of recent bot output audio for echo correlation.

    Parameters
    ----------
    buffer_seconds:
        How many seconds of bot audio to retain. Must cover the
        worst-case acoustic delay (speaker → room → mic) plus the
        longest expected mic chunk. 1.0 s is generous.
    target_sample_rate:
        The sample rate the buffer operates at. Must match the mic
        input rate (16 kHz in this pipeline).
    """

    def __init__(
        self,
        buffer_seconds: float = 1.0,
        target_sample_rate: int = 16000,
    ) -> None:
        self._target_sr = target_sample_rate
        self._buf_len = int(buffer_seconds * target_sample_rate)
        self._ring = np.zeros(self._buf_len, dtype=np.float32)
        self._write_pos = 0
        self._filled = False
        self._bot_speaking = False
        self._bot_stopped_at: float = 0.0

    @property
    def bot_speaking(self) -> bool:
        return self._bot_speaking

    @property
    def bot_stopped_at(self) -> float:
        """Monotonic timestamp of the last BotStoppedSpeakingFrame."""
        return self._bot_stopped_at

    def set_bot_speaking(self, speaking: bool) -> None:
        self._bot_speaking = speaking
        if not speaking:
            self._bot_stopped_at = time.monotonic()

    def add_bot_audio(self, pcm_s16le: bytes, source_sample_rate: int) -> None:
        """Append bot output audio to the ring buffer.

        ``pcm_s16le`` is signed 16-bit little-endian mono PCM at
        ``source_sample_rate`` (typically 24 kHz from TTS). It is
        resampled to ``target_sample_rate`` before storage.
        """
        samples = np.frombuffer(pcm_s16le, dtype=np.int16).astype(np.float32)
        if len(samples) == 0:
            return

        if source_sample_rate != self._target_sr:
            ratio = self._target_sr / source_sample_rate
            new_len = max(1, int(len(samples) * ratio))
            indices = np.linspace(0, len(samples) - 1, new_len)
            samples = np.interp(indices, np.arange(len(samples)), samples)

        n = len(samples)
        if n >= self._buf_len:
            self._ring[:] = samples[-self._buf_len :]
            self._write_pos = 0
            self._filled = True
            return

        end = self._write_pos + n
        if end <= self._buf_len:
            self._ring[self._write_pos : end] = samples
        else:
            first = self._buf_len - self._write_pos
            self._ring[self._write_pos :] = samples[:first]
            self._ring[: n - first] = samples[first:]
            self._filled = True
        self._write_pos = end % self._buf_len

    def get_buffer(self) -> np.ndarray:
        """Return the ring buffer contents in chronological order."""
        if self._filled:
            return np.roll(self._ring, -self._write_pos)
        return self._ring.copy()

    def clear(self) -> None:
        """Zero the buffer (e.g. on mode switch or device change)."""
        self._ring[:] = 0.0
        self._write_pos = 0
        self._filled = False


__all__ = ["EchoState"]
