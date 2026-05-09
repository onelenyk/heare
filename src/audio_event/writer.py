"""Atomic JSON writer for the audio_event.json dashboard contract.

Lives next to the observer in :mod:`src.audio_event` so the side-effect
file write is co-located with its only caller. Mirrors the atomic
tmpfile + ``os.replace`` pattern used by ``write_voice_state``.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path


logger = logging.getLogger("heare.audio_event")


def write_audio_event(path: Path, label: str, score: float) -> None:
    """Write the most recent confirmed audio event to *path*.

    The schema is intentionally tiny — the dashboard reads it on every
    tick and auto-decays stale entries by comparing ``ts`` to the wall
    clock. Failures are logged, never raised, so a transient write
    error cannot deadlock the audio loop.
    """
    payload = {
        "label": label,
        "score": round(float(score), 3),
        "ts": time.time(),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload))
        os.replace(tmp, path)
    except OSError:
        logger.exception("audio_event: failed to write %s", path)


__all__ = ["write_audio_event"]
