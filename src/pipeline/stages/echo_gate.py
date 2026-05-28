"""Acoustic echo cancellation gate — audio-level echo suppression.

Two pipeline stages share an ``EchoState`` ring buffer:

* ``BotAudioCollector`` sits downstream of TTS. It captures every
  ``TTSAudioRawFrame`` into the ring buffer and tracks
  ``BotStartedSpeakingFrame`` / ``BotStoppedSpeakingFrame``.

* ``MicEchoGate`` sits upstream of STT (after ``input_mute_gate``).
  For each ``InputAudioRawFrame`` arriving while the bot is speaking
  (or within the post-speech cooldown), it cross-correlates the mic
  audio against the bot buffer. When the normalised peak correlation
  exceeds ``threshold``, the frame is dropped — STT never sees it.

This operates at the audio level, before STT, so it is immune to
transcription errors that defeat text-based echo detection.

Pipecat imports are deferred so admin CLI paths work without portaudio.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from src.config import Settings
    from src.pipeline.echo_state import EchoState


logger = logging.getLogger("heare.echo_gate")


def _load_pipecat_base():
    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        Frame,
        InputAudioRawFrame,
        TTSAudioRawFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    return (
        FrameProcessor,
        FrameDirection,
        Frame,
        InputAudioRawFrame,
        TTSAudioRawFrame,
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
    )


_collector_cls: type | None = None
_gate_cls: type | None = None


def _build_classes():
    global _collector_cls, _gate_cls
    if _collector_cls is not None and _gate_cls is not None:
        return _collector_cls, _gate_cls

    (
        FrameProcessor,
        FrameDirection,
        Frame,
        InputAudioRawFrame,
        TTSAudioRawFrame,
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
    ) = _load_pipecat_base()

    class BotAudioCollector(FrameProcessor):  # type: ignore[misc,valid-type]
        """Captures bot TTS output audio into the shared EchoState."""

        def __init__(self, echo_state: "EchoState") -> None:
            super().__init__()
            self._echo_state = echo_state

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)

            if isinstance(frame, BotStartedSpeakingFrame):
                self._echo_state.set_bot_speaking(True)
                await self.push_frame(frame, direction)
                return

            if isinstance(frame, BotStoppedSpeakingFrame):
                self._echo_state.set_bot_speaking(False)
                await self.push_frame(frame, direction)
                return

            if isinstance(frame, TTSAudioRawFrame):
                sr = getattr(frame, "sample_rate", 24000)
                self._echo_state.add_bot_audio(frame.audio, sr)

            await self.push_frame(frame, direction)

    class MicEchoGate(FrameProcessor):  # type: ignore[misc,valid-type]
        """Drops mic audio that correlates with recent bot output."""

        def __init__(
            self,
            echo_state: "EchoState",
            *,
            threshold: float = 0.3,
            cooldown_seconds: float = 0.5,
        ) -> None:
            super().__init__()
            self._echo_state = echo_state
            self._threshold = threshold
            self._cooldown_seconds = cooldown_seconds
            self._dropped = 0
            self._passed = 0

        def _is_active(self) -> bool:
            if self._echo_state.bot_speaking:
                return True
            stopped = self._echo_state.bot_stopped_at
            if stopped > 0 and time.monotonic() - stopped < self._cooldown_seconds:
                return True
            return False

        @staticmethod
        def _peak_correlation(mic: np.ndarray, bot: np.ndarray) -> float:
            if len(mic) == 0 or len(bot) == 0:
                return 0.0
            mic_c = mic - np.mean(mic)
            bot_c = bot - np.mean(bot)
            mic_energy = np.sqrt(np.sum(mic_c ** 2))
            if mic_energy < 1e-6:
                return 0.0
            corr = np.correlate(bot_c, mic_c, mode="valid")
            if len(corr) == 0:
                return 0.0
            bot_len = len(mic_c)
            n_windows = len(corr)
            bot_sq_cumsum = np.cumsum(np.concatenate(([0.0], bot_c ** 2)))
            bot_energy = np.sqrt(
                bot_sq_cumsum[bot_len:bot_len + n_windows]
                - bot_sq_cumsum[:n_windows]
            )
            norm = mic_energy * bot_energy
            norm = np.where(norm < 1e-10, 1.0, norm)
            return float(np.max(np.abs(corr) / norm))

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)

            if isinstance(frame, InputAudioRawFrame) and self._is_active():
                mic = np.frombuffer(frame.audio, dtype=np.int16).astype(np.float32)
                bot = self._echo_state.get_buffer()
                peak = self._peak_correlation(mic, bot)
                if peak >= self._threshold:
                    self._dropped += 1
                    if self._dropped <= 3 or self._dropped % 50 == 0:
                        logger.debug(
                            "[ECHO GATE] drop corr=%.3f threshold=%.2f dropped=%d",
                            peak, self._threshold, self._dropped,
                        )
                    return
                self._passed += 1

            await self.push_frame(frame, direction)

    _collector_cls = BotAudioCollector
    _gate_cls = MicEchoGate
    return _collector_cls, _gate_cls


def create_echo_gate_stages(
    echo_state: "EchoState",
    settings: "Settings",
) -> tuple[Any, Any]:
    """Factory returning ``(BotAudioCollector, MicEchoGate)`` instances."""
    collector_cls, gate_cls = _build_classes()
    collector = collector_cls(echo_state)
    gate = gate_cls(
        echo_state,
        threshold=settings.echo_gate_threshold,
        cooldown_seconds=settings.echo_gate_cooldown_seconds,
    )
    return collector, gate


__all__ = ["create_echo_gate_stages"]
