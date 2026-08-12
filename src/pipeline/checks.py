"""What a scenario must show, stated so a machine can decide it.

Until now a room scenario printed a timeline and said PASS if it had
heard anything and had not heard itself. Everything else — did it reply,
did it reply twice, did the answer contain the number it went and
fetched, did the interruption land inside a budget — was left to whoever
was reading the output. Which meant it was left to nobody.

A check takes a result and returns the reasons it failed, empty when it
held. They compose: a scenario lists several, and the run reports every
reason at once rather than stopping at the first.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol


class Result(Protocol):
    """The part of a room result checks are allowed to see."""

    heard: list[str]
    said: list[str]
    heard_itself: int
    barge_in_ms: float | None
    bot_utterances: int
    events: list[Any]


Check = Callable[[Result], list[str]]


def _norm(text: str) -> str:
    return " ".join(text.lower().split()).strip(" .,!?—«»")


# ── what must not happen ──────────────────────────────────────────────


def never_hears_itself() -> Check:
    """The failure that hid four separate bugs: its own voice returning
    through the microphone and being answered as if it were the user."""

    def check(r: Result) -> list[str]:
        if r.heard_itself:
            return [f"heard itself {r.heard_itself}×"]
        return []

    return check


def stays_silent() -> Check:
    """For speech that was never addressed to it. A podcast in the room
    used to produce a turn every few seconds."""

    def check(r: Result) -> list[str]:
        n = max(_reply_count(r), r.bot_utterances)
        if n:
            return [f"replied {n}× to speech meant for nobody"]
        return []

    return check


# ── what must happen ──────────────────────────────────────────────────


def _reply_count(r: Result) -> int:
    """Finished replies, not gaps in the audio.

    Counting gaps was close enough until the speaker started playing in
    real time. Then a delegated job that finished while the
    acknowledgement was still being spoken came out as one unbroken
    fifteen-second stretch of sound — two replies, one silence between
    them, and the run failed for having "replied 1×" while the recording
    plainly held both.
    """
    spoken = getattr(r, "spoken", None)
    return len(spoken) if spoken else r.bot_utterances


def replies(at_least: int = 1, at_most: int | None = None) -> Check:
    """How many separate replies. Two is right for delegated work — an
    acknowledgement and an answer — and three means it acknowledged
    twice, which is the bug that shipped this morning."""

    def check(r: Result) -> list[str]:
        n = _reply_count(r)
        if n < at_least:
            return [f"replied {n}×, expected at least {at_least}"]
        if at_most is not None and n > at_most:
            return [f"replied {n}×, expected at most {at_most}"]
        return []

    return check


def heard(*fragments: str) -> Check:
    """Speech recognition got the words. Loosely — Whisper punctuates and
    capitalises as it pleases, and one dropped ending is not a failure of
    the pipeline."""

    def check(r: Result) -> list[str]:
        joined = _norm(" ".join(r.heard))
        return [f"never heard {f!r}" for f in fragments if _norm(f) not in joined]

    return check


def barge_in_under(ms: float) -> Check:
    """An interruption that arrives late is an interruption that did not
    happen: the user has already repeated themselves."""

    def check(r: Result) -> list[str]:
        if r.barge_in_ms is None:
            return ["barge-in never fired"]
        if r.barge_in_ms > ms:
            return [f"barge-in took {r.barge_in_ms:.0f} ms, budget {ms:.0f} ms"]
        return []

    return check


def first_reply_under(seconds: float) -> Check:
    """The gap between the last word said and the first sound back. Six
    seconds of it were two timers nobody had measured."""

    def check(r: Result) -> list[str]:
        spoke = [e for e in r.events if e.kind == "bot_started"]
        said = [e for e in r.events if e.kind == "said"]
        if not spoke:
            return ["never spoke"]
        if not said:
            return []
        gap = (spoke[0].at - said[0].at) / 1000
        if gap > seconds:
            return [f"first reply {gap:.1f}s after being asked, budget {seconds:.1f}s"]
        return []

    return check


def eventually_says(*fragments: str) -> Check:
    """The delegated answer actually reaches the user's ears.

    The worker found "488 GB free" and the assistant said "секунду,
    гляну" a second time. The result arrived and was dropped, and every
    counter said the run had passed.
    """

    def check(r: Result) -> list[str]:
        spoken = _norm(" ".join(getattr(r, "spoken", []) or []))
        return [f"never said {f!r}" for f in fragments if _norm(f) not in spoken]

    return check


def run_checks(checks: list[Check], result: Result) -> list[str]:
    """Every reason it failed, not just the first."""
    failures: list[str] = []
    for check in checks:
        failures.extend(check(result))
    return failures


__all__ = [
    "Check",
    "barge_in_under",
    "eventually_says",
    "first_reply_under",
    "heard",
    "never_hears_itself",
    "replies",
    "run_checks",
    "stays_silent",
]
