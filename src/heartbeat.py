"""Fires every N minutes and asks the decider to check in.

The heartbeat is dumb — the decider owns the policy for whether to speak.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .decider import DeciderProcessor  # noqa: F401


logger = logging.getLogger("heare.heartbeat")


class HeartbeatTask:
    def __init__(self, decider_processor, interval_minutes: int) -> None:
        self.decider_processor = decider_processor
        self.interval_seconds = max(1, interval_minutes) * 60
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        logger.info("heartbeat task started (interval=%ss)", self.interval_seconds)
        try:
            while not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self.interval_seconds
                    )
                    break
                except asyncio.TimeoutError:
                    pass
                try:
                    await self.decider_processor.on_heartbeat_tick()
                except Exception as e:
                    logger.exception("heartbeat tick failed: %s", e)
        except asyncio.CancelledError:
            logger.info("heartbeat task cancelled")
            raise
        finally:
            logger.info("heartbeat task stopped")

    def stop(self) -> None:
        self._stop_event.set()
