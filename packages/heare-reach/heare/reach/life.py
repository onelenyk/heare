"""Giving work a life: a total timeout, an idle timeout, and a stop.

An agent that says nothing is not thinking — it is stuck: a dropped socket, an
expired key, a model call that never returns. A total timeout keeps one task
from running all afternoon; an idle timeout catches silence while the rest of
the work is fine. Both stop the work and say which happened.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")

# The watchdog's own heartbeat; nothing here needs to be finer than this.
POLL_S = 0.5


class LifeOver(RuntimeError):
    """The work was stopped: too long in total, or silent for too long."""


async def guarded(
    work: Awaitable[T],
    last: Callable[[], float],
    *,
    idle_s: float,
    total_s: float,
    stop: Callable[[], Awaitable[None]] | None = None,
) -> T:
    """Await ``work``; stop it if it goes silent for ``idle_s`` or runs past
    ``total_s``. ``last`` says when the work last showed a sign of life; ``stop``
    is told to stop it on the way out."""
    task = asyncio.ensure_future(work)
    started = time.monotonic()
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=POLL_S)
            if done:
                return await task
            now = time.monotonic()
            if now - started > total_s:
                raise LifeOver(f"занадто довго: понад {int(total_s)} с")
            if now - last() > idle_s:
                raise LifeOver(f"агент мовчить понад {int(idle_s)} с")
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if stop is not None:
                try:
                    await stop()
                except Exception:  # noqa: BLE001 — stopping must not raise over the reason
                    pass
