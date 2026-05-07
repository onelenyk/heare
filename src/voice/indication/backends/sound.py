"""SoundBackend — pre-rendered PCM cues injected via SoundCueProcessor.

The backend's `fire()` is called by the Indication facade on the main loop.
It looks up the pre-built PCM for the cue's level and hands it to the
SoundCueProcessor's queue, which emits the bracketed frame sequence
(IndicationCueFrame(start=True) → OutputAudioRawFrame → IndicationCueFrame(start=False))
into the pipecat pipeline.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.voice.indication import assets as indication_assets

if TYPE_CHECKING:
    from src.voice.indication.core import IndicationKind, IndicationLevel, SoundCueProcessor

logger = logging.getLogger("heare.indication.sound")


class SoundBackend:
    name = "sound"

    def __init__(
        self,
        processor: "SoundCueProcessor",
        sample_rate: int = indication_assets.DEFAULT_SAMPLE_RATE,
    ) -> None:
        self._processor = processor
        self._cues: dict[str, bytes] = indication_assets.all_cues(sample_rate)

    async def fire(
        self,
        kind: "IndicationKind",
        level: "IndicationLevel",
        title: str,
        body: str,
        meta: dict,
    ) -> None:
        pcm = self._cues.get(level.value)
        if pcm is None:
            logger.debug("sound: no cue for level %s; skipping", level.value)
            return
        self._processor.enqueue_cue(pcm)

    async def aclose(self) -> None:
        await self._processor.aclose()
