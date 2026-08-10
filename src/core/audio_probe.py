"""A tap on the microphone path, for when barge-in does not happen.

Interruption depends on the user's voice surviving the trip from the
microphone to the VAD analyzer in the user aggregator. Several stages
modify or drop audio on the way, and when nothing interrupts there is no
way to tell which one ate it — the pipeline reports silence identically
whether the room was quiet or a filter zeroed the frame.

This logs the level at a named point once a second, with a marker for
whether the bot was speaking. Insert two and the difference tells you
which stage is responsible.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

logger = logging.getLogger("audio_probe")


def make_audio_probe(label: str, echo_state: Any, period: float = 1.0) -> Any:
    from pipecat.frames.frames import InputAudioRawFrame
    from pipecat.processors.frame_processor import FrameProcessor

    class AudioProbe(FrameProcessor):
        def __init__(self) -> None:
            super().__init__()
            self._last = 0.0
            self._peak_quiet = 0.0
            self._peak_talking = 0.0
            self._frames = 0

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)

            if isinstance(frame, InputAudioRawFrame) and frame.audio:
                import numpy as np

                pcm = np.frombuffer(frame.audio, dtype=np.int16)
                if pcm.size:
                    rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2)))
                    db = 20 * math.log10(max(rms, 1e-9) / 32768.0)
                    self._frames += 1
                    if echo_state.bot_speaking:
                        self._peak_talking = max(self._peak_talking, db)
                    else:
                        self._peak_quiet = max(self._peak_quiet, db)

                now = time.monotonic()
                if now - self._last >= period:
                    self._last = now
                    logger.info(
                        "%-9s frames=%3d  peak(bot silent)=%6.1f dB  "
                        "peak(bot talking)=%6.1f dB",
                        label,
                        self._frames,
                        self._peak_quiet,
                        self._peak_talking,
                    )
                    self._frames = 0
                    self._peak_quiet = -120.0
                    self._peak_talking = -120.0

            await self.push_frame(frame, direction)

    return AudioProbe()
