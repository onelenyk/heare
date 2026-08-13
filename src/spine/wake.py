"""A wake gate that only the assistant can refresh.

pipecat's WakePhraseUserTurnStartStrategy has a documented defect: it
refreshes its window on ANY transcription — the room's noise as much as
speech addressed to the assistant. See tests/test_wake_window.py for the
characterisation tests that name it, including the recorded consequence:
a "Дока, привіт!" and a film playing held the gate open for four and a
half minutes.

This module replaces that gate at the text level. The daemon transcribes
everything regardless — hearing is not gated — but only turns this gate
accepts reach the LLM. The fix is where the refresh comes from: never
from a turn this gate itself rejected, only from one it accepted as
addressed to the assistant.
"""

from __future__ import annotations

import time
from typing import Callable


def _is_word_char(ch: str) -> bool:
    """Unicode-aware "this belongs to a word" test, Cyrillic included."""
    return ch.isalnum() or ch == "_"


def _contains_phrase(text: str, phrase: str) -> bool:
    """Case-insensitive, whole-word substring match.

    Not `re` with `\\b`: whether `\\b` treats Cyrillic as a word
    character depends on the UNICODE flag being active on the pattern,
    which is easy to get wrong silently. Scanning characters directly
    with str.isalnum() removes the doubt — it is Unicode-aware by
    definition, not by flag.
    """
    lower_phrase = phrase.strip().lower()
    if not lower_phrase:
        return False
    lower_text = text.lower()
    n = len(lower_phrase)
    start = 0
    while True:
        idx = lower_text.find(lower_phrase, start)
        if idx == -1:
            return False
        before_ok = idx == 0 or not _is_word_char(lower_text[idx - 1])
        after = idx + n
        after_ok = after >= len(lower_text) or not _is_word_char(lower_text[after])
        if before_ok and after_ok:
            return True
        start = idx + 1


class WakeGate:
    """Awake/asleep by wake phrase, immune to background noise.

    The window is refreshed ONLY by accepted turns — speech that was
    addressed to the assistant — never by arbitrary transcription. That
    is the fix for the noise-holds-the-gate-open defect.
    """

    def __init__(
        self,
        phrases: list[str],
        window_s: float = 45.0,
        required: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._phrases = [p for p in phrases if p.strip()]
        self._window_s = window_s
        self._required = required
        self._clock = clock
        self._awake = not required
        self._last_accept: float | None = None

    def _has_phrase(self, turn_text: str) -> bool:
        return any(_contains_phrase(turn_text, phrase) for phrase in self._phrases)

    def accepts(self, turn_text: str) -> bool:
        """True if this turn should reach the LLM. Wakes on a turn
        containing any phrase (case-insensitive, whole-word match — a
        phrase inside another word must not wake). An accepted turn
        refreshes the window; a rejected one must NOT. required=False
        accepts everything.
        """
        if not self._required:
            return True

        now = self._clock()
        woke_now = self._has_phrase(turn_text)
        within_window = (
            self._awake
            and self._last_accept is not None
            and (now - self._last_accept) <= self._window_s
        )

        if woke_now or within_window:
            self._awake = True
            self._last_accept = now
            return True

        self._awake = False
        return False

    @property
    def awake(self) -> bool:
        return self._awake

    def sleep(self) -> None:
        """Force asleep (e.g. after "спи")."""
        self._awake = False
        self._last_accept = None


__all__ = ["WakeGate"]
