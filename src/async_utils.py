"""Async utility functions — lightweight, no heavy imports."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("heare.tasks")


def safe_task(coro, *, name: str = "") -> asyncio.Task:
    """Create a background task that logs exceptions instead of silently dying.

    Use this instead of bare ``asyncio.create_task()`` for fire-and-forget
    background tasks. If the task raises an unhandled exception, it will be
    logged at ERROR level rather than silently swallowed by the event loop.
    """
    task = asyncio.create_task(coro, name=name)
    task.add_done_callback(_log_task_exception)
    return task


def _log_task_exception(task: asyncio.Task) -> None:
    try:
        exc = task.exception()
        if exc is not None:
            logger.exception(
                "Background task %r crashed", task.get_name(), exc_info=exc
            )
    except (asyncio.CancelledError, asyncio.InvalidStateError):
        pass
