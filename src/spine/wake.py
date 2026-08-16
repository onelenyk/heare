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

Two clocks, on purpose
----------------------
The 45-second window is measured with `time.monotonic()`, because "how
long since the last accepted turn" must not be perturbed by an NTP step
or by someone changing the system clock. But a monotonic clock is the
right tool for "how long since" and the wrong tool for "did the world
move on": on macOS and Linux it does not advance while the machine is
suspended. Close the lid mid-conversation, sleep for four hours, open
it — `now - last_accept` is still a few seconds, the gate is still
AWAKE, and the first sentence anyone says in the room goes to the LLM
without the wake word. The privacy promise lapses exactly when the user
was away.

So the gate reads a second, wall clock (`time.time`) alongside the
monotonic one and keeps both samples. Between two calls, wall time and
monotonic time should advance together; when wall time runs ahead of
monotonic time by more than `suspend_threshold_s`, the missing
monotonic seconds are seconds the machine was not running. That is a
suspend, and the gate goes back to sleep — a wake phrase is required
again.

The rule is deliberately one-sided (wall ahead of monotonic, never the
reverse) and its failure mode is deliberately safe: a forward NTP step
of more than the threshold puts the gate to sleep, which costs the user
one wake word and never costs them the promise.
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

    Both clocks are injectable so a test can simulate a machine suspend
    without suspending anything: `clock` (monotonic, measures the
    window) and `wall` (wall time, detects the suspend). A test that
    injects only `clock` sees no divergence worth the threshold and
    behaves exactly as it did before this existed.
    """

    def __init__(
        self,
        phrases: list[str],
        window_s: float = 45.0,
        required: bool = True,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        suspend_threshold_s: float = 5.0,
    ) -> None:
        self._phrases = [p for p in phrases if p.strip()]
        self._window_s = window_s
        self._required = required
        self._clock = clock
        self._wall = wall
        self._suspend_threshold_s = suspend_threshold_s
        self._awake = not required
        self._last_accept: float | None = None
        self._seen_mono: float | None = None
        self._seen_wall: float | None = None

    def _has_phrase(self, turn_text: str) -> bool:
        return any(_contains_phrase(turn_text, phrase) for phrase in self._phrases)

    def _sample(self) -> bool:
        """Take both clocks; True if they diverged like a suspend.

        Divergence = the wall seconds that elapsed minus the monotonic
        seconds that elapsed since the previous sample. Positive and
        large means wall time moved while the monotonic clock did not:
        the machine was not running. The first sample has nothing to
        compare against and never reports a suspend.
        """
        now_mono = self._clock()
        now_wall = self._wall()
        last_mono, last_wall = self._seen_mono, self._seen_wall
        self._seen_mono, self._seen_wall = now_mono, now_wall
        if last_mono is None or last_wall is None:
            return False
        drift = (now_wall - last_wall) - (now_mono - last_mono)
        return drift > self._suspend_threshold_s

    def check_suspend(self) -> bool:
        """Sleep the gate if the machine was suspended since the last
        call. Returns True when that happened.

        Called automatically from accepts(), so nothing has to remember
        it: the first turn spoken after the lid opens is already gated.
        A caller that polls (the engine's loop) may call it directly so
        `awake` is honest in the display before anyone speaks.
        """
        suspended = self._sample()
        if suspended and self._required:
            self.sleep()
        return suspended

    def accepts(self, turn_text: str) -> bool:
        """True if this turn should reach the LLM. Wakes on a turn
        containing any phrase (case-insensitive, whole-word match — a
        phrase inside another word must not wake). An accepted turn
        refreshes the window; a rejected one must NOT. required=False
        accepts everything.

        A machine suspend since the previous call closes the window
        first: the window says "a few seconds ago" only because the
        monotonic clock was asleep too.
        """
        self.check_suspend()

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
