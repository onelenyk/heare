"""Text injection — let an external process feed text into the daemon as
if it were a finalized STT transcript.

The dashboard writes one file per message into a drop-folder; a daemon-side
poller reads + deletes each file and pushes a ``TranscriptionFrame`` into
the pipeline (just upstream of the transcription_gate, the same place STT
output lands).

File-queue + delete is robust: the daemon can crash mid-process and the
unread messages stay on disk for next start.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger("heare.text_injector")


def queue_dir(base: Path) -> Path:
    """The on-disk drop folder. ``base`` is normally ``settings.inject_dir``."""
    base.mkdir(parents=True, exist_ok=True)
    return base


def inject_text(base: Path, text: str) -> Path:
    """Write ``text`` to a fresh file in the queue. Returns the path written.

    Used by the watch dashboard — synchronous and tiny, no daemon round-trip.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("inject_text: empty text")
    folder = queue_dir(base)
    # Filename starts with monotonic-ish timestamp so directory listing is
    # naturally chronological. UUID suffix avoids collisions on rapid sends.
    name = f"{time.time():.6f}-{uuid.uuid4().hex[:8]}.txt"
    path = folder / name
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.rename(path)  # atomic publish
    return path


def _drain_once(folder: Path) -> list[tuple[Path, str]]:
    """Return [(path, text), ...] for every queued message; newest last."""
    if not folder.exists():
        return []
    items: list[tuple[Path, str]] = []
    for p in sorted(folder.iterdir()):
        if p.suffix != ".txt":
            continue
        try:
            text = p.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            items.append((p, text))
    return items


async def run_injector_loop(
    folder: Path,
    push: Callable[[str], Awaitable[None]],
    *,
    poll_interval: float = 0.25,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Async polling loop. Reads + deletes every queued file, awaits ``push``.

    ``push`` is the async callback the daemon passes — it builds a
    ``TranscriptionFrame`` and feeds it into the pipeline.
    """
    queue_dir(folder)
    while True:
        if stop_event is not None and stop_event.is_set():
            return
        for path, text in _drain_once(folder):
            try:
                await push(text)
                logger.info("injected text: %s", text[:80])
            except Exception:
                logger.exception("inject push failed for %s", path)
            finally:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        await asyncio.sleep(poll_interval)


def make_transcription_pusher(
    target: Any,
    *,
    user_id: str = "injected",
    language: str | None = None,
) -> Callable[[str], Awaitable[None]]:
    """Build a callback suitable for ``run_injector_loop``'s ``push`` arg.

    ``target`` is any pipecat ``FrameProcessor`` whose ``push_frame`` method
    forwards into the pipeline (typically the ``transcription_gate``).
    """

    async def _push(text: str) -> None:
        from pipecat.frames.frames import TranscriptionFrame
        from pipecat.transcriptions.language import Language

        lang_obj: Any = None
        if language:
            try:
                lang_obj = Language(language)
            except (ValueError, KeyError):
                lang_obj = None

        frame = TranscriptionFrame(
            text=text,
            user_id=user_id,
            timestamp=str(time.time()),
            language=lang_obj,
            finalized=True,
        )
        await target.push_frame(frame)

    return _push


__all__ = [
    "inject_text",
    "make_transcription_pusher",
    "queue_dir",
    "run_injector_loop",
]
