"""In-memory PCM cache for fixed phrases the assistant says often.

Stores raw s16le PCM bytes keyed on the original text. Cached phrases bypass
edge-tts entirely and play back in microseconds, eliminating ~1-5 seconds of
TTS latency for repeated lines like 'okay', 'nevermind, cancelled', etc.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger("heare.tts_cache")

Synthesizer = Callable[[str], Awaitable[bytes]]


class TTSCache:
    """Simple in-memory cache: text → PCM bytes."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    def get(self, text: str) -> bytes | None:
        return self._store.get(text)

    def put(self, text: str, pcm: bytes) -> None:
        self._store[text] = pcm

    def has(self, text: str) -> bool:
        return text in self._store

    def __len__(self) -> int:
        return len(self._store)

    async def warmup(self, phrases: list[str], synthesizer: Synthesizer) -> None:
        """Pre-render every phrase that isn't already cached.

        Synthesizes missing phrases concurrently via asyncio.gather. Per-phrase
        failures are counted and logged as a summary — a missing cache entry
        just falls back to live TTS.
        """
        missing = [p for p in phrases if p not in self._store]
        if not missing:
            return
        logger.info("warming up TTS cache for %d phrase(s)", len(missing))

        success = 0
        failed = 0

        async def _synth_one(phrase: str) -> None:
            nonlocal success, failed
            try:
                pcm = await synthesizer(phrase)
            except asyncio.CancelledError:
                raise
            except Exception:
                failed += 1
                return
            if pcm:
                self._store[phrase] = pcm
                success += 1
            else:
                failed += 1

        await asyncio.gather(*(_synth_one(p) for p in missing))
        total = len(missing)
        if failed:
            logger.info(
                "TTS cache: %d/%d phrases cached (%d failed — Edge TTS unavailable for those)",
                success,
                total,
                failed,
            )
            if failed == total:
                logger.warning("TTS cache: all %d phrases failed to pre-cache", total)
        else:
            logger.info("TTS cache populated: %d entries", len(self._store))
