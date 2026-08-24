"""A wake gate that only the assistant can refresh.

pipecat's WakePhraseUserTurnStartStrategy has a documented defect: it
refreshes its window on ANY transcription — the room's noise as much as
speech addressed to the assistant. The characterisation of that defect,
with its recorded consequence — a "Дока, привіт!" and a film playing held
the gate open for four and a half minutes — is in tests/test_spine_wake.py
(`test_rejected_turns_do_not_refresh_the_window`); it used to live in
tests/test_wake_window.py, which went with the pipecat engine.

This module replaces that gate at the text level. The daemon transcribes
everything regardless — hearing is not gated — but only turns this gate
accepts reach the LLM. The fix is where the refresh comes from: never
from a turn this gate itself rejected, only from one it accepted as
addressed to the assistant.

Delegation reopens the same question from the other direction. "Дока,
перевір пошту" wakes the gate; the worker runs for 40-70 seconds with
nobody speaking; the result is injected and spoken with no accepted
mic turn anywhere in that gap. The user answers at once, by name of
nothing — no wake word, because the assistant just addressed them and
a reply to that is exactly what an open line is for. Without a way for
the gate to learn the assistant spoke, `_last_accept` is minutes stale
by then and that reply is rejected as noise. `spoke()` is the fix: it
refreshes the window the same way an accepted turn does, but see its
docstring for why it only ever EXTENDS an open window and never WAKES
a sleeping one.

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

A window that grows with the conversation
-----------------------------------------
Forty-five seconds is the right leash for a single command and the
wrong one for talking. Observed 24 August: an exchange ran four turns
with no wake word — the window worked exactly as designed — and then
the person looked at the screen for fifty-three seconds, spoke, and had
to say the name again. The gap between "still talking to it" and "not
talking to it any more" is not fixed at forty-five seconds; it is
longer the deeper the conversation is.

So the window is `window_s` multiplied by the number of consecutive
accepted turns, capped at `max_window_s`. One command still lapses in
forty-five seconds. A five-turn conversation holds for three minutes.

The reasoning is about risk, not comfort: the chance that a wake was
accidental is highest at the first turn and falls with every exchange
after it. A room does not produce four consecutive turns addressed to
the assistant by accident, and if the first one was a mistake the
second one is not coming.

Only accepted turns count. `spoke()` refreshes the window but never
lengthens it, for the same reason it never wakes a sleeping gate: the
assistant must not be able to buy itself a longer leash by talking.
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
    is the fix for the noise-holds-the-gate-open defect. It is also
    refreshed by `spoke()`, called when the assistant addresses the
    user (a reply, or a delegated result arriving with no mic turn in
    between) — see that method's docstring for why it extends but
    never wakes.

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
        max_window_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        suspend_threshold_s: float = 5.0,
    ) -> None:
        self._phrases = [p for p in phrases if p.strip()]
        self._window_s = window_s
        # Four turns' worth by default. Past that the conversation is
        # not getting any more obviously a conversation, and the room
        # keeps whatever is said in it for however long this holds.
        self._max_window_s = (
            max_window_s if max_window_s is not None else window_s * 4
        )
        self._required = required
        # Consecutive accepted turns. The window is this many times
        # `window_s`, so a single command lapses quickly and a real
        # exchange does not. Reset by sleep(), and therefore by suspend.
        self._streak = 0
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
            and (now - self._last_accept) <= self.window_s
        )

        if woke_now or within_window:
            self._awake = True
            self._last_accept = now
            self._streak += 1
            return True

        self.sleep()
        return False

    def spoke(self) -> None:
        """Tell the gate the assistant just addressed the user out loud.

        Refreshes the window exactly like an accepted turn — EXCEPT
        that it never wakes a sleeping gate on its own; it only extends
        one that is already open.

        Why not wake: if the assistant's own voice could open the gate,
        the assistant could talk itself awake — any spoken output
        (a proactive line, a notification, a stray retry) would grant
        itself standing to then treat the next thing anyone says as
        addressed to it, no wake word required. That is the exact
        privacy promise this module exists to keep: only speech
        addressed BY the user, wake phrase or already-open window,
        should let the room's next sentence through.

        Why extend at all, then: a delegated result is not the
        assistant talking to itself — it is the assistant answering a
        request the user already made, addressed to the assistant, that
        happened to take 40-70 seconds. The window was opened by a real
        address; the reply landing outside the original 45s is a
        product delay, not a new solicitation. A phrase-less "дякую" or
        a follow-up right after hearing that reply is the same
        conversation the wake word already authorised, and it must not
        cost a second wake word just because the worker was slow.

        So: `spoke()` while `awake` is True pushes `_last_accept`
        forward, same as `accepts()` returning True. `spoke()` while
        `awake` is False is a no-op on the window — a proactive or
        stray utterance with nobody having said the wake word first
        stays exactly as closed as it was.

        Suspend is checked first, with the same two clocks `accepts()`
        uses, and it wins over the extension: a lid closed right after
        the assistant spoke — or during the delegated work, before the
        result was ever spoken — must still put the gate back to sleep.
        A machine that slept does not get to skip the wake word just
        because the last thing before it slept was the assistant's own
        voice.
        """
        self.check_suspend()
        if self._required and self._awake:
            self._last_accept = self._clock()

    @property
    def window_s(self) -> float:
        """How long the line stays open, given how long it has been open.

        `window_s` per consecutive accepted turn, capped. Zero turns is
        the base window rather than zero seconds: the gate is asked this
        before the waking turn is counted.
        """
        return min(self._window_s * max(self._streak, 1), self._max_window_s)

    @property
    def awake(self) -> bool:
        return self._awake

    def sleep(self) -> None:
        """Force asleep (e.g. after "спи"). The next conversation starts
        on the short leash again — a streak is a property of one
        exchange, not a credit the room accumulates."""
        self._awake = False
        self._last_accept = None
        self._streak = 0


__all__ = ["WakeGate"]
