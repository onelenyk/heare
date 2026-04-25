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
                try:
                    from .indication import IndicationKind, get_indication

                    ind = get_indication()
                    if ind is not None:
                        ind.notify(IndicationKind.HEARTBEAT_TICK)
                except Exception:  # noqa: BLE001
                    logger.warning("heartbeat indication notify failed", exc_info=True)
        except asyncio.CancelledError:
            logger.info("heartbeat task cancelled")
            raise
        finally:
            logger.info("heartbeat task stopped")

    def stop(self) -> None:
        self._stop_event.set()


class WarmupTask:
    """Periodically pings edge-tts to keep the WebSocket connection warm.

    Cold edge-tts reconnect costs ~150ms. Without periodic activity the WSS
    connection drops after a few minutes. Sending a one-character utterance
    every ~4 minutes keeps it alive at ~zero cost.
    """

    # Production floor — guards against hot-loop misconfiguration.
    # Tests that need short intervals patch this attribute.
    _MIN_INTERVAL_SECONDS: float = 1.0

    def __init__(self, voice: str, interval_seconds: float = 240.0) -> None:
        self.voice = voice
        self.interval_seconds = max(self._MIN_INTERVAL_SECONDS, float(interval_seconds))
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        logger.info("warmup task started (interval=%ss)", self.interval_seconds)
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
                    await self._ping_edge_tts()
                except Exception as e:
                    logger.warning("warmup ping failed (non-fatal): %s", e)
        except asyncio.CancelledError:
            logger.info("warmup task cancelled")
            raise
        finally:
            logger.info("warmup task stopped")

    async def _ping_edge_tts(self) -> None:
        # Imported lazily so tests can patch edge_tts without pulling pipecat.
        import edge_tts

        comm = edge_tts.Communicate(".", self.voice)
        async for chunk in comm.stream():
            # Discard everything; we only need the connection to be touched.
            if chunk.get("type") == "audio":
                continue

    def stop(self) -> None:
        self._stop_event.set()
