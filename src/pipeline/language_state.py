"""Shared mutable language state for the Phase 2 Pipecat-native pipeline.

PH2-04. The TranscriptionGateProcessor (pre-LLM) decides the active
user language (with 2-turn hysteresis); the DeepSeekLLM service
(LLM turn) needs to read that decision so it can inject the right
``{user_language}`` into the system prompt for each turn.

Pre-PH2 this was a private attribute on GeneratorProcessor. Once the
gate and the LLM service live in different processors, neither
should reach into the other's private state — this module is the
single object both sides share.

Design notes:
* The state is a plain-Python class with a non-async API. Reads and
  writes are synchronous; if multi-pipeline contention ever becomes
  a concern, the asyncio event loop's single-threaded execution
  already serialises producer/consumer access on the same loop.
* The state intentionally has no Pipecat dependencies — admin CLI
  paths can import it without portaudio installed.
* Writers MAY register a no-arg callback via ``set_change_listener``;
  it fires only when the language actually changes. PH2-02 uses this
  hook to push an ``LLMUpdateSettingsFrame`` into the pipeline so
  the LLM service rebuilds the system prompt for the next turn
  without polling.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional


logger = logging.getLogger("heare.language_state")


class LanguageState:
    """Single source of truth for the active user language."""

    def __init__(self, *, initial: str = "en") -> None:
        self._language: str = initial
        self._listener: Optional[Callable[[str], None]] = None

    @property
    def language(self) -> str:
        """Read the current active language tag (e.g. ``'en'``, ``'uk'``,
        ``'ru'``)."""
        return self._language

    def set_language(self, lang: str) -> bool:
        """Update the active language. Returns True if the value
        changed, False if the input matched the existing value.
        """
        if not lang:
            return False
        if lang == self._language:
            return False
        old = self._language
        self._language = lang
        logger.info("[LANGUAGE STATE] %s -> %s", old, lang)
        if self._listener is not None:
            try:
                self._listener(lang)
            except Exception:
                logger.exception(
                    "language_state: listener raised (non-fatal)"
                )
        return True

    def set_change_listener(
        self, listener: Optional[Callable[[str], None]]
    ) -> None:
        """Register a callback fired when ``set_language`` changes the
        value. Pass ``None`` to clear. Only one listener is supported —
        re-registering replaces the previous binding.
        """
        self._listener = listener


__all__ = ["LanguageState"]
