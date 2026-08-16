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


# -- 8. a machine suspend closes the window ---------------------------------
#
# time.monotonic() does not advance while the machine is suspended, so a
# window measured with it alone survives a night of sleep: "45 seconds
# since the last accepted turn" is true only because the clock slept too.
# The gate reads a wall clock beside it; wall time running ahead of
# monotonic time is time the machine was not running. Both clocks are
# injectable, so these tests simulate a suspend without suspending.


def test_a_suspend_puts_the_gate_back_to_sleep() -> None:
    """The lid was closed mid-conversation and opened an hour later.

    On the monotonic clock only two seconds passed, so the 45s window is
    wide open and the first sentence anyone says in the room would go to
    the LLM with no wake word. The wall clock says an hour. The gate has
    to be asleep.
    """
    mono = Clock(100.0)
    wall = Clock(1_700_000_000.0)
    gate = WakeGate(
        phrases=doka_phrases(),
        window_s=45.0,
        clock=as_clock(mono),
        wall=as_clock(wall),
    )

    assert gate.accepts("дока привіт") is True
    assert gate.awake is True

    # lid closed; one hour of wall time, two seconds of monotonic time
    mono.set(102.0)
    wall.set(1_700_000_000.0 + 3600.0)

    assert gate.accepts("а це я вже комусь іншому в кімнаті") is False
    assert gate.awake is False

    # and it is not wedged: the wake phrase still works afterwards
    mono.set(103.0)
    wall.set(1_700_000_000.0 + 3601.0)
    assert gate.accepts("докер, я повернувся") is True
    assert gate.awake is True


def test_a_normal_gap_is_not_a_suspend() -> None:
    """Ten seconds where both clocks advance together is just a pause in
    the conversation — the window must stay open."""
    mono = Clock(0.0)
    wall = Clock(1_700_000_000.0)
    gate = WakeGate(
        phrases=doka_phrases(),
        window_s=45.0,
        clock=as_clock(mono),
        wall=as_clock(wall),
    )

    assert gate.accepts("дока привіт") is True

    mono.set(10.0)
    wall.set(1_700_000_000.0 + 10.0)

    assert gate.accepts("а тепер по суті") is True
    assert gate.awake is True


def test_small_clock_jitter_is_not_a_suspend() -> None:
    """Clocks do not tick in lockstep — a scheduler hiccup or a small NTP
    slew must not cost the user a wake word. Only a divergence past the
    threshold counts."""
    mono = Clock(0.0)
    wall = Clock(1_700_000_000.0)
    gate = WakeGate(
        phrases=doka_phrases(),
        window_s=45.0,
        clock=as_clock(mono),
        wall=as_clock(wall),
        suspend_threshold_s=5.0,
    )

    assert gate.accepts("дока привіт") is True

    mono.set(5.0)
    wall.set(1_700_000_000.0 + 9.0)  # 4s of divergence, under the threshold

    assert gate.accepts("продовжуємо") is True
    assert gate.awake is True


def test_check_suspend_reports_and_sleeps_without_a_turn() -> None:
    """The engine can poll it, so `awake` is honest before anyone speaks."""
    mono = Clock(0.0)
    wall = Clock(1_700_000_000.0)
    gate = WakeGate(
        phrases=doka_phrases(),
        window_s=45.0,
        clock=as_clock(mono),
        wall=as_clock(wall),
    )

    assert gate.accepts("дока привіт") is True
    assert gate.check_suspend() is False  # nothing happened yet
    assert gate.awake is True

    mono.set(1.0)
    wall.set(1_700_000_000.0 + 7200.0)
    assert gate.check_suspend() is True
    assert gate.awake is False


# -- 9. spoke() — the delegate-result defect, fixed --------------------
#
# "Дока, перевір пошту" wakes the gate. The worker runs 40-70s with
# nobody speaking. The result is injected and spoken with no accepted
# mic turn in between, so `_last_accept` is stale by the time the user
# answers without the wake word. `spoke()` is how the gate learns the
# assistant addressed the user, so that reply is not treated as noise.


def test_a_spoken_result_extends_the_window_for_the_next_phrase_less_reply() -> None:
    """Without calling `gate.spoke()` here, this test fails exactly the
    way the audit found the defect: `accepts()` at t=60 sees the last
    accepted turn was 60s ago, more than the 45s window, and rejects
    the user's reply — even though the assistant just spoke to them."""
    clock = Clock()
    gate = WakeGate(phrases=doka_phrases(), window_s=45.0, clock=as_clock(clock))

    assert gate.accepts("дока, перевір пошту") is True  # wakes at t=0

    clock.set(60.0)  # the worker took its time; nobody spoke in between
    gate.spoke()  # the delegated result is spoken to the user

    assert gate.accepts("дякую, зрозуміло") is True
    assert gate.awake is True


def test_a_suspend_right_after_speaking_still_closes_the_gate() -> None:
    """The lid can close right after the assistant's own voice, same as
    after the user's. `spoke()` samples both clocks itself (the same
    suspend check `accepts()` uses), so a suspend in the gap between
    one spoken line and the next is not missed just because no accepted
    turn happened in between to notice it."""
    mono = Clock(0.0)
    wall = Clock(1_700_000_000.0)
    gate = WakeGate(
        phrases=doka_phrases(),
        window_s=45.0,
        clock=as_clock(mono),
        wall=as_clock(wall),
    )

    assert gate.accepts("дока, перевір пошту") is True  # wakes at t=0

    mono.set(1.0)
    wall.set(1_700_000_000.0 + 1.0)
    gate.spoke()  # "Прийнято, роблю" — clocks still in lockstep
    assert gate.awake is True

    # lid closes while the worker runs: two seconds of monotonic time
    # pass for an hour of wall time
    mono.set(3.0)
    wall.set(1_700_000_000.0 + 3601.0)
    gate.spoke()  # the delegated result is spoken as the lid reopens
    assert gate.awake is False

    mono.set(4.0)
    wall.set(1_700_000_000.0 + 3602.0)
    assert gate.accepts("а це я вже комусь іншому в кімнаті") is False
    assert gate.awake is False


def test_spoke_does_not_wake_a_sleeping_gate() -> None:
    """Pins the chosen rule: the assistant's own voice may EXTEND an
    already-open window but must never WAKE a sleeping one. If it could,
    any spoken output — a proactive line, a notification, a stray retry
    — would grant the assistant standing to treat the room's next
    sentence as addressed to it, no wake word required. That is exactly
    the promise this gate exists to keep."""
    clock = Clock()
    gate = WakeGate(phrases=doka_phrases(), window_s=45.0, clock=as_clock(clock))

    assert gate.awake is False  # nobody has said the wake word yet

    gate.spoke()  # e.g. a proactive line racing ahead of any wake turn
    assert gate.awake is False

    clock.set(1.0)
    assert gate.accepts("продовжуємо без жодної фрази") is False
    assert gate.awake is False


def test_required_false_is_unaffected_by_a_suspend() -> None:
    """With the wake word not required there is no promise to keep: the
    gate must not report itself asleep after a suspend."""
    mono = Clock(0.0)
    wall = Clock(1_700_000_000.0)
    gate = WakeGate(
        phrases=["докер"],
        window_s=45.0,
        required=False,
        clock=as_clock(mono),
        wall=as_clock(wall),
    )

    mono.set(1.0)
    wall.set(1_700_000_000.0 + 3600.0)

    assert gate.accepts("будь-що після сну") is True
    assert gate.awake is True
