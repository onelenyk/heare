"""Background tasks that run alongside the pipeline.

Currently only ``WarmupTask`` lives here. The legacy proactive-speech
ticker (every N minutes) was removed in US-WU-04; see the matching
refactor commit and ``.omc/progress.txt``.
"""

from __future__ import annotations

import asyncio
import logging


logger = logging.getLogger("heare.heartbeat")


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

        comm = edge_tts.Communicate("ok", self.voice)
        async for chunk in comm.stream():
            # Discard everything; we only need the connection to be touched.
            if chunk.get("type") == "audio":
                continue

    def stop(self) -> None:
        self._stop_event.set()
