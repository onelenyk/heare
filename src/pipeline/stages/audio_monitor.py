"""Sidetone — copies mic input to speaker so the user can monitor
exactly what the agent hears.

Provides ``SidetoneProcessor``, a FrameProcessor that sits between the
echo gate and STT. For every ``InputAudioRawFrame`` it resamples the
16 kHz mic audio to the output sample rate (24 kHz) and pushes an
``OutputAudioRawFrame`` downstream toward the speaker transport.

Sidetone is gated OFF during bot speech to prevent feedback loops,
and can be toggled via the ``sidetone`` state key and flag file.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameProcessor

logger = logging.getLogger("heare.sidetone")


class SidetoneProcessor(FrameProcessor):
    """Copies mic input (16 kHz) → speaker output (24 kHz) with volume control.

    Gated OFF while the bot is speaking (``BotStartedSpeakingFrame`` /
    ``BotStoppedSpeakingFrame``) to prevent feedback loops.

    Enabled/disabled via the ``sidetone`` state key or the
    ``~/.heare/sidetone.flag`` file.
    """

    def __init__(
        self,
        state: Any,
        settings: Any,
        sample_rate_in: int = 16000,
        sample_rate_out: int = 24000,
        volume: float = 0.5,
    ) -> None:
        super().__init__()
        self._state = state
        self._settings = settings
        self._sample_rate_in = sample_rate_in
        self._sample_rate_out = sample_rate_out
        self._volume = volume
        self._bot_speaking = False

    @staticmethod
    def _resample(
        audio: np.ndarray,
        orig_sr: int,
        target_sr: int,
    ) -> np.ndarray:
        """Linear resample from *orig_sr* to *target_sr*."""
        new_len = int(len(audio) * target_sr / orig_sr)
        old_indices = np.linspace(0, len(audio) - 1, len(audio))
        new_indices = np.linspace(0, len(audio) - 1, new_len)
        return np.interp(new_indices, old_indices, audio).astype(np.float32)

    async def process_frame(self, frame: Frame, direction: int) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            logger.debug("sidetone: gated OFF — bot started speaking")
            await self.push_frame(frame, direction)

        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            logger.debug("sidetone: gated ON — bot stopped speaking")
            await self.push_frame(frame, direction)

        elif isinstance(frame, InputAudioRawFrame):
            enabled = self._state.get_bool("sidetone") or self._settings.sidetone_file.exists()
            if not enabled or self._bot_speaking:
                await self.push_frame(frame, direction)
                return

            # frame.audio is bytes (PCM int16). Convert to float32 for
            # resampling, then back to bytes for OutputAudioRawFrame.
            audio_np = np.frombuffer(frame.audio, dtype=np.int16).astype(np.float32)
            monitor_audio = self._resample(
                audio_np,
                self._sample_rate_in,
                self._sample_rate_out,
            )
            monitor_audio = np.clip(
                monitor_audio * self._volume, -32768, 32767
            ).astype(np.int16)

            await self.push_frame(
                OutputAudioRawFrame(
                    audio=monitor_audio.tobytes(),
                    sample_rate=self._sample_rate_out,
                    num_channels=1,
                ),
                direction,
            )
            await self.push_frame(frame, direction)

        else:
            await self.push_frame(frame, direction)
