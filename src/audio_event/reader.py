"""Reader for the audio_event.json contract.

Co-located with :mod:`src.audio_event.writer` so the read side of the
tiny on-disk schema lives next to the write side. Used by the
transcription gate to tag user turns with the ambient sound context
(e.g. "Music" was playing) so the LLM can treat short or garbled
transcripts skeptically.

The dashboard has its own dataclass-based reader in ``src/watch/data.py``
that auto-decays stale entries against the wall clock; that path is
unchanged. This module returns the raw triple and leaves freshness /
threshold policy to the caller, since the gate's window differs from the
dashboard's display TTL.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("heare.audio_event")


def read_latest_audio_event(path: Path) -> tuple[str, float, float] | None:
    """Return ``(label, score, ts)`` for the last confirmed event, or None.

    Returns ``None`` when the file is missing, unreadable, malformed, or
    carries no label — callers treat that as "no ambient context".
    """
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, OSError, ValueError):
        return None
    label = raw.get("label")
    if not label:
        return None
    try:
        score = float(raw.get("score", 0.0))
        ts = float(raw.get("ts", 0.0))
    except (TypeError, ValueError):
        return None
    return str(label), score, ts


__all__ = ["read_latest_audio_event"]
