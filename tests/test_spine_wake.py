"""Tests for WakeGate — the fix for the noise-holds-the-gate-open defect.

tests/test_wake_window.py characterises that defect in pipecat's own
WakePhraseUserTurnStartStrategy and is written to fail once the gate is
replaced. These tests characterise the replacement: text-level, no
asyncio, no sleeps — time comes from a fake clock backed by a mutable
list, advanced by hand between calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Callable

from src.pipeline.wake import wake_phrases
from src.spine.wake import WakeGate


class Clock:
    """A fake clock backed by a one-item mutable list.

    Time only moves when `set` is called — never by sleeping — which is
    what lets these tests exercise a 45-second window in microseconds.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._t = [start]

    def __call__(self) -> float:
        return self._t[0]

    def set(self, t: float) -> None:
        self._t[0] = t


def as_clock(clock: Clock) -> Callable[[], float]:
    return clock


def doka_phrases() -> list[str]:
    """The real phrase list build.py resolves for the default wake word.

    Reused rather than re-hardcoded: "докер" et al. come from
    src/pipeline/wake.py's own variant table (wake_phrases -> _variants),
    the same framework-free function the daemon calls.
    """
    settings = SimpleNamespace(wake_word="doka")
    return wake_phrases(settings, persona="")


# -- 1. asleep by default when required -------------------------------------


def test_asleep_by_default_when_required() -> None:
    clock = Clock()
    gate = WakeGate(phrases=doka_phrases(), window_s=45.0, required=True, clock=as_clock(clock))

    assert gate.awake is False
    assert gate.accepts("зовсім не про нього") is False
    assert gate.awake is False


# -- 2. wakes on phrase, accepts that turn -----------------------------------


def test_wakes_on_phrase_and_accepts_that_turn() -> None:
    clock = Clock()
    gate = WakeGate(phrases=doka_phrases(), window_s=45.0, clock=as_clock(clock))

    assert gate.accepts("дока привіт") is True
    assert gate.awake is True


# -- 3. within the window, phrase-less turns are accepted --------------------


def test_within_window_accepts_phrase_less_turns() -> None:
    clock = Clock()
    gate = WakeGate(phrases=doka_phrases(), window_s=45.0, clock=as_clock(clock))

    assert gate.accepts("дока привіт") is True  # wakes at t=0

    clock.set(10.0)
    assert gate.accepts("а тепер по суті") is True
    assert gate.awake is True

    clock.set(40.0)  # 30s since the last accepted turn at t=10, still < 45s
    assert gate.accepts("ще одне речення без імені") is True
    assert gate.awake is True


# -- 4. the fix: a rejected turn never refreshes the window ------------------


def test_rejected_turns_do_not_refresh_the_window() -> None:
    """The defect stated in tests/test_wake_window.py, undone.

    pipecat's strategy refreshed its timeout on every transcription,
    noise included, so a film playing nearby held the gate open for
    four and a half minutes. Here a rejected (phrase-less, out-of-window)
    turn must not wake the gate and must not extend the window for the
    next phrase-less turn either — no creeping refresh from noise.
    """
    clock = Clock()
    gate = WakeGate(phrases=doka_phrases(), window_s=45.0, clock=as_clock(clock))

    assert gate.accepts("дока привіт") is True  # wakes at t=0

    clock.set(50.0)  # the 45s window since t=0 has expired
    assert gate.accepts("Дякую за перегляд! Фредди, ніфі мій.") is False
    assert gate.awake is False

    clock.set(55.0)  # still no phrase: still rejected, no creeping refresh
    assert gate.accepts("ще одна репліка з телевізора") is False
    assert gate.awake is False

    clock.set(60.0)  # only a phrase wakes it again
    assert gate.accepts("докер, привіт") is True
    assert gate.awake is True


# -- 5. whole-word matching, Cyrillic-safe -----------------------------------


def test_whole_word_match_wakes_on_the_phrase() -> None:
    clock = Clock()
    gate = WakeGate(phrases=["докер"], window_s=45.0, clock=as_clock(clock))

    assert gate.accepts("докер, привіт") is True
    assert gate.awake is True


def test_whole_word_match_ignores_non_matching_word() -> None:
    clock = Clock()
    gate = WakeGate(phrases=["докер"], window_s=45.0, clock=as_clock(clock))

    assert gate.accepts("стикер") is False
    assert gate.awake is False


def test_whole_word_match_rejects_phrase_embedded_in_a_longer_word() -> None:
    clock = Clock()
    gate = WakeGate(phrases=["докер"], window_s=45.0, clock=as_clock(clock))

    assert gate.accepts("докерфайл не збирається") is False
    assert gate.awake is False


# -- 6. required=False accepts everything, always awake ----------------------


def test_required_false_accepts_everything_and_stays_awake() -> None:
    clock = Clock()
    gate = WakeGate(phrases=["докер"], window_s=45.0, required=False, clock=as_clock(clock))

    assert gate.awake is True
    assert gate.accepts("що завгодно, без жодної фрази") is True
    assert gate.awake is True

    clock.set(10_000.0)
    assert gate.accepts("і це теж, значно пізніше") is True
    assert gate.awake is True


# -- 7. sleep() forces asleep immediately, even mid-window -------------------


def test_sleep_forces_asleep_mid_window() -> None:
    clock = Clock()
    gate = WakeGate(phrases=doka_phrases(), window_s=45.0, clock=as_clock(clock))

    assert gate.accepts("дока привіт") is True
    assert gate.awake is True

    clock.set(10.0)  # well inside the still-open 45s window
    gate.sleep()
    assert gate.awake is False

    # the window had not expired, but sleep() overrides it: a phrase-less
    # turn is rejected exactly as it would be after a real timeout
    assert gate.accepts("продовжуємо розмову") is False
    assert gate.awake is False

    clock.set(11.0)
    assert gate.accepts("докер, я тут") is True
    assert gate.awake is True
