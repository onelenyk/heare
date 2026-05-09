"""Voice-state observer: writes the live STT state to a JSON file.

The watch dashboard polls this file on its refresh tick to render the
current voice state (idle / listening / stt / result). Pattern mirrors
``mute_file`` — a tiny disk-backed contract between the daemon and the
dashboard so neither needs IPC.

State machine driven by Pipecat frames:

    UserStartedSpeakingFrame   → "listening"
    UserStoppedSpeakingFrame   → "stt"     (audio captured, awaiting STT)
    InterimTranscriptionFrame  → "stt"     (partial text refresh)
    TranscriptionFrame         → "result"  (final text)

The widget treats "result" as transient — if ``now - since_ts`` exceeds
its display window, it renders idle. Keeping the auto-decay on the
reader side avoids the writer needing a timer.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from pipecat.frames.frames import (
    InterimTranscriptionFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


logger = logging.getLogger("heare.voice_state")


def write_voice_state(
    path: Path,
    state: str,
    *,
    last_partial: str | None = None,
    last_final: str | None = None,
) -> None:
    """Atomically write the current voice state to *path*.

    Writes to a sibling tmpfile then ``os.replace`` so a concurrent
    reader never sees a half-written JSON document.
    """
    payload: dict[str, Any] = {
        "state": state,
        "since_ts": time.time(),
        "last_partial": last_partial,
        "last_final": last_final,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload))
        os.replace(tmp, path)
    except OSError:
        logger.exception("voice_state: failed to write %s", path)


class VoiceStateObserver(FrameProcessor):
    """Pipeline pass-through that mirrors STT state into a JSON file.

    Forwards every frame unchanged; the only side effect is the
    ``write_voice_state`` call on the four transition frames listed in
    the module docstring.
    """

    def __init__(self, state_file: Path) -> None:
        super().__init__()
        self._state_file = state_file
        self._last_partial: str | None = None
        self._last_final: str | None = None

    async def process_frame(self, frame, direction: FrameDirection) -> None:  # type: ignore[override]
        await super().process_frame(frame, direction)
        if isinstance(frame, UserStartedSpeakingFrame):
            self._last_partial = None
            write_voice_state(self._state_file, "listening")
        elif isinstance(frame, UserStoppedSpeakingFrame):
            write_voice_state(
                self._state_file, "stt", last_partial=self._last_partial
            )
        elif isinstance(frame, InterimTranscriptionFrame):
            self._last_partial = (frame.text or "").strip() or self._last_partial
            write_voice_state(
                self._state_file, "stt", last_partial=self._last_partial
            )
        elif isinstance(frame, TranscriptionFrame):
            self._last_final = (frame.text or "").strip() or self._last_final
            write_voice_state(
                self._state_file, "result", last_final=self._last_final
            )
        await self.push_frame(frame, direction)


def create_voice_state_observer(state_file: Path) -> VoiceStateObserver:
    return VoiceStateObserver(state_file)


__all__ = [
    "VoiceStateObserver",
    "create_voice_state_observer",
    "write_voice_state",
]
