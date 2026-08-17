"""Text injection — let an external process feed text to the agent as if
the user had said it.

A drop-folder with one file per message. The writer is synchronous and
tiny; the daemon polls, reads and deletes. File-queue-and-delete is
robust in the way that matters here: the daemon can die mid-message and
nothing is lost, because the unread files are still on disk at the next
start.

This used to live in ``src/pipeline/stages/text_injector.py``, alongside
a pipecat frame pusher. The queue has nothing to do with any engine —
the dashboard, the HTTP API and the daemon all write to it — so it
outlived the pipeline it was filed under.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

logger = logging.getLogger("heare.inject")


def queue_dir(base: Path) -> Path:
    """The on-disk drop folder. ``base`` is normally ``settings.inject_dir``."""
    base.mkdir(parents=True, exist_ok=True)
    return base


def inject_text(base: Path, text: str) -> Path:
    """Write ``text`` to a fresh file in the queue. Returns the path written."""
    text = (text or "").strip()
    if not text:
        raise ValueError("inject_text: empty text")
    folder = queue_dir(base)
    # The name starts with a timestamp so a directory listing is already
    # in order; the uuid suffix keeps two messages in the same
    # microsecond from colliding.
    name = f"{time.time():.6f}-{uuid.uuid4().hex[:8]}.txt"
    path = folder / name
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.rename(path)  # atomic publish: a reader never sees a half-written file
    return path


def drain(folder: Path) -> list[tuple[Path, str]]:
    """Return ``[(path, text), ...]`` for every queued message, oldest first."""
    if not folder.exists():
        return []
    items: list[tuple[Path, str]] = []
    for p in sorted(folder.iterdir()):
        if p.suffix != ".txt":
            continue
        try:
            text = p.read_text(encoding="utf-8").strip()
        except OSError:
            logger.exception("inject: could not read %s", p)
            continue
        if text:
            items.append((p, text))
    return items
