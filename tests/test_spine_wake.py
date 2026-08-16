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

from src.spine.wake import WakeGate
from src.spine.wake_phrases import _name_from_persona, wake_phrases


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
    """The real phrase list the spine resolves for the default wake word.

    Reused rather than re-hardcoded: "докер" et al. come from
    src/spine/wake_phrases.py's own variant table (wake_phrases ->
    _variants), the same framework-free function main.py calls.
    """
    settings = SimpleNamespace(wake_word="doka")
    return wake_phrases(settings, persona="")


# -- 0. the phrase table itself ---------------------------------------------
#
# Copied from tests/test_wake.py, which covers the same logic where it used
# to live (src/pipeline/wake.py). Duplicated on purpose: the spine's copy
# has to stay covered once the old tree goes.

PERSONA = "You are Doka 🎧 — A capable ambient AI that lives in your headphones."


def test_the_name_comes_from_the_generated_identity() -> None:
    """It is generated at first run, so nobody can hard-code it."""
    assert _name_from_persona(PERSONA) == "Doka"
    assert _name_from_persona("You are Kort, an assistant.") == "Kort"
    assert _name_from_persona("") == ""


def test_phrases_cover_what_speech_recognition_actually_produces() -> None:
    """Matching is exact-word over the transcript, so the list has to hold
    what Whisper writes, not how the name is spelled. In one session it
    produced "докер", "дока" and "Дока" for the same word."""
    phrases = wake_phrases(SimpleNamespace(wake_word="гава"), PERSONA)

    for heard in ("doka", "дока", "докер"):
        assert heard in phrases


def test_both_the_name_and_the_wake_word_are_listened_for() -> None:
    """People call it by name; the wake word is a separate setting that
    may never have been changed from its default."""
    phrases = wake_phrases(SimpleNamespace(wake_word="гава"), PERSONA)

    assert "doka" in phrases
    assert "гава" in phrases


def test_the_list_is_never_empty() -> None:
    """An empty phrase list would gate on nothing and block every turn —
    the assistant would go permanently deaf."""
    assert wake_phrases(SimpleNamespace(wake_word=""), "") == ["гава"]


def test_no_duplicates() -> None:
    phrases = wake_phrases(SimpleNamespace(wake_word="doka"), PERSONA)
    assert len(phrases) == len(set(phrases))


def test_the_spine_resolves_phrases_without_importing_pipecat() -> None:
    """The reason this table was copied: src/spine/main.py used to load it
    by file path, because importing src.pipeline.wake runs that package's
    __init__ and pulls pipecat into the spine."""
    import subprocess
    import sys as _sys
    from pathlib import Path

    code = (
        "import sys, src.spine.wake_phrases as w;"
        "print(any('pipecat' in m for m in sys.modules))"
    )
    out = subprocess.run(
        [_sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "False"


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
