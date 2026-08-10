"""Do not answer things nobody said.

Whisper is a generative model. Handed a segment of silence or room noise
it does not return nothing — it returns the most likely text, which for
this recording setup is "Дякую." Observed live, eight times in ninety
seconds, interleaved with "І серпу." and a sentence of invented Ukrainian.

Each of those is a complete turn: a model call, a synthesis, an
utterance. The user's real questions queue up behind them, and the
assistant appears slow when it is in fact busy answering a room.

VAD lets them through because the thresholds are generous — confidence
0.3, minimum volume 0.1, 100 ms to open — against a microphone amplified
2.4×. Tuning those helps, but it is a knob-turning exercise that has to
be redone per room and per gain setting. This gate is the invariant
underneath: a segment too quiet to be speech is not speech, whatever the
transcript claims, and a known filler phrase from a quiet segment is a
hallucination rather than a word.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger("heare.speech_energy")

# Peak RMS below this is a room, not a voice. Measured on this hardware:
# silence sits near 30 (-60 dBFS), speech runs 1000 and up (-30 dBFS).
DEFAULT_MIN_RMS = 180.0
DEFAULT_MIN_SECONDS = 0.30

# What Whisper says when it has nothing to say. Only ever dropped when
# the segment was also short — the user is allowed to thank it.
FILLERS = frozenset(
    {
        "дякую",
        "дякую.",
        "дякую!",
        "дякую за перегляд",
        "дякую за перегляд!",
        "дякуємо за перегляд",
        "продовження буде",
        "субтитри",
        "thank you",
        "thank you.",
        "thanks for watching",
        "thanks for watching!",
        "you",
        "bye",
        "bye.",
    }
)
FILLER_MAX_SECONDS = 1.5


def _normalize(text: str) -> str:
    return " ".join(text.lower().replace("!", "").replace("…", "").split()).strip(" .,")


def create_speech_energy_gate(
    *,
    min_rms: float = DEFAULT_MIN_RMS,
    min_seconds: float = DEFAULT_MIN_SECONDS,
    sample_rate: int = 16000,
) -> Any:
    """A stage that drops transcripts of segments nobody spoke.

    Sits after STT, where both the audio and the resulting transcript are
    visible, so the decision needs no state shared across stages.
    """
    from pipecat.frames.frames import (
        InputAudioRawFrame,
        InterimTranscriptionFrame,
        TranscriptionFrame,
        UserStartedSpeakingFrame,
    )
    from pipecat.processors.frame_processor import FrameProcessor

    class SpeechEnergyGate(FrameProcessor):
        def __init__(self) -> None:
            super().__init__()
            self._peak = 0.0
            self._samples = 0
            self._dropped_quiet = 0
            self._dropped_filler = 0

        def _reset(self) -> None:
            self._peak = 0.0
            self._samples = 0

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)

            if isinstance(frame, UserStartedSpeakingFrame):
                self._reset()

            elif isinstance(frame, InputAudioRawFrame) and frame.audio:
                pcm = np.frombuffer(frame.audio, dtype=np.int16)
                if pcm.size:
                    rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2)))
                    self._peak = max(self._peak, rms)
                    self._samples += pcm.size

            elif isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
                text = (getattr(frame, "text", "") or "").strip()
                seconds = self._samples / sample_rate
                verdict = self._verdict(text, seconds)
                if verdict is not None:
                    logger.info(
                        "[SPEECH GATE] dropped (%s): %r  peak=%.0f dur=%.2fs "
                        "(quiet=%d filler=%d)",
                        verdict,
                        text[:60],
                        self._peak,
                        seconds,
                        self._dropped_quiet,
                        self._dropped_filler,
                    )
                    return

            await self.push_frame(frame, direction)

        def _verdict(self, text: str, seconds: float) -> str | None:
            """Why this transcript should be dropped, or None to keep it."""
            if not text:
                return None
            if self._samples == 0:
                return None  # injected text, not microphone audio
            if seconds < min_seconds:
                self._dropped_quiet += 1
                return "too short"
            if self._peak < min_rms:
                self._dropped_quiet += 1
                return "too quiet"
            if _normalize(text) in FILLERS and seconds < FILLER_MAX_SECONDS:
                self._dropped_filler += 1
                return "filler"
            return None

    return SpeechEnergyGate()


__all__ = ["create_speech_energy_gate", "FILLERS", "DEFAULT_MIN_RMS"]
