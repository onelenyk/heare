"""The voice-state file the dashboard and menubar read.

The watch dashboard renders the current voice state (idle / listening /
stt / result) from this document. "result" is treated as transient by the
reader — if ``now - since_ts`` exceeds its display window it renders idle,
so the writer never needs a timer.

This is the writer only. The pipecat frame processor that used to drive it
stays with the old engine; the spine calls ``write_voice_state`` directly
from the points in its loop that know what is happening, which is why this
module imports nothing beyond the standard library.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

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


__all__ = ["write_voice_state"]
