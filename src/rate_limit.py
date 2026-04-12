"""Best-effort request-rate limiter.

Used to cap our own claude_cli decider calls so we don't blow through the
Groq free tier. GroqSTTService runs inside pipecat and is not something we
can intercept from here — document that tradeoff in the README.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Callable

logger = logging.getLogger("heare.rate_limit")


class RateLimiter:
    def __init__(
        self,
        max_calls: int,
        window_seconds: float = 60.0,
        *,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], "asyncio.Future[None]"] | None = None,
    ) -> None:
        self.max_calls = max(1, max_calls)
        self.window_seconds = window_seconds
        self._clock = clock or (lambda: asyncio.get_event_loop().time())
        self._sleeper = sleeper or asyncio.sleep
        self._history: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = self._clock()
            self._prune(now)
            if len(self._history) < self.max_calls:
                self._history.append(now)
                return
            earliest = self._history[0]
            wait_for = (earliest + self.window_seconds) - now
            if wait_for > 0:
                logger.warning(
                    "rate limit: sleeping %.2fs (used %d/%d in last %ss)",
                    wait_for,
                    len(self._history),
                    self.max_calls,
                    self.window_seconds,
                )
                await self._sleeper(wait_for)
            now = self._clock()
            self._prune(now)
            self._history.append(now)

    def _prune(self, now: float) -> None:
        while self._history and (now - self._history[0]) >= self.window_seconds:
            self._history.popleft()
