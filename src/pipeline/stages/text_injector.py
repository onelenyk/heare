"""Text injection — let an external process feed text to the agent as if the
user had said it.

The dashboard writes one file per message into a drop-folder; a daemon-side
poller reads + deletes each file and appends it to the LLM context, asking
for a completion in the same frame.

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

    ``push`` is the async callback the daemon passes — see
    :func:`make_llm_message_pusher`.
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


def make_llm_message_pusher(
    target: Any,
    *,
    role: str = "user",
) -> Callable[[str], Awaitable[None]]:
    """Build a callback suitable for ``run_injector_loop``'s ``push`` arg.

    ``target`` is normally the transcription gate, which exposes
    ``inject_user_text`` — the shared entry point that persists the turn,
    picks the TTS voice for it, and emits a pipeline event before pushing it
    at the LLM. Delegating keeps one owner for "what happens to a user turn";
    an earlier version of this function reimplemented a subset of that work
    and the two promptly drifted.

    A ``target`` without that method (a bare ``FrameProcessor``) falls back to
    pushing the frame directly, so the injector stays usable in isolation.

    Either way the frame is ``LLMMessagesAppendFrame``, never
    ``TranscriptionFrame``, which is subtle enough to be worth recording.
    ``LLMUserAggregator._handle_transcription`` only appends the text to an
    internal buffer; that buffer is flushed to the LLM by
    ``push_aggregation()``, which is reached solely via the configured user
    turn *stop* strategy. This pipeline uses ``TurnAnalyzerUserTurnStopStrategy``
    (and ``VADUserTurnStartStrategy``), both driven by microphone audio — so a
    frame arriving without audio never completes a turn. Worse, the start
    strategy never calls ``trigger_reset_aggregation``, so the stranded text is
    not discarded either: it stays buffered and is prepended to whatever the
    user says next.

    ``LLMMessagesAppendFrame(run_llm=True)`` sidesteps the turn machinery
    entirely — the aggregator adds the message and pushes the context frame
    directly.
    """

    async def _push(text: str) -> None:
        inject = getattr(target, "inject_user_text", None)
        if role == "user" and callable(inject):
            await inject(text)
            return

        from pipecat.frames.frames import LLMMessagesAppendFrame

        await target.push_frame(
            LLMMessagesAppendFrame(
                messages=[{"role": role, "content": text}],
                run_llm=True,
            )
        )

    return _push


__all__ = [
    "inject_text",
    "make_llm_message_pusher",
    "queue_dir",
    "run_injector_loop",
]
