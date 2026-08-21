"""Where it is, in time and in space.

The prompt always carried a timestamp — `2026-08-18 01:08:00`, last line,
every turn. A model can read that; it cannot easily act on it. Told the
hour as a number it reasons about a number; told it is a Tuesday night on
a laptop in Kyiv it reasons about a person who is probably tired and
probably alone.

These pin the difference, and the two ways of getting it wrong: printing
a sentinel as if it were a fact, and printing the same claim twice in two
different shapes.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.spine.situation import UNKNOWN_SILENCE_S, observe


def at(stamp: str) -> float:
    return time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M"))


def described(now: float, **kw) -> str:
    return asyncio.run(observe(now=now, **kw)).describe()


class _Persist:
    """Both clocks have to be the same clock: reading time.time() here
    while the situation is built from a fixed `now` puts the test's own
    runtime into the answer."""

    def __init__(self, now: float, quiet_for: float) -> None:
        self.last = now - quiet_for

    def last_turn_times(self):
        return {"any": self.last, "user": self.last}


# ── time, as a person would say it ────────────────────────────────────


def test_it_says_the_weekday_and_the_clock() -> None:
    line = described(at("2026-08-18 01:08"))
    assert line.startswith("вівторок, 01:08")


@pytest.mark.parametrize(
    "stamp, expected",
    [
        ("2026-08-18 02:00", "ніч"),
        ("2026-08-18 09:00", "ранок"),
        ("2026-08-18 14:00", "день"),
        ("2026-08-18 20:00", "вечір"),
        ("2026-08-18 23:00", "ніч"),
    ],
)
def test_the_hour_is_named_not_just_numbered(stamp: str, expected: str) -> None:
    assert expected in described(at(stamp))


def test_night_is_a_property_not_a_guess() -> None:
    """Whispering at 02:00 is not a judgement call, and nothing should
    have to infer it from a timestamp."""
    night = asyncio.run(observe(now=at("2026-08-18 02:00")))
    day = asyncio.run(observe(now=at("2026-08-18 14:00")))
    assert night.is_night is True
    assert day.is_night is False


# ── space ─────────────────────────────────────────────────────────────


def test_it_knows_where_it_is() -> None:
    """Nothing told it before — the spine's prompt did not carry even the
    host OS the old pipeline used to."""
    line = described(at("2026-08-18 14:00"))
    assert any(word in line for word in ("макбук", "linux", "windows"))


def test_the_place_reads_as_a_phrase_not_a_hostname() -> None:
    """`MacBook-Pro-M4--Nazar.local` is a fact about DNS. What goes in
    front of a language model should be readable."""
    line = described(at("2026-08-18 14:00"))
    assert ".local" not in line
    assert "--" not in line
    assert "  " not in line


# ── silence ───────────────────────────────────────────────────────────


def test_a_real_gap_is_reported() -> None:
    now = at("2026-08-18 14:00")
    assert "тиша 40 хв" in described(now, persist=_Persist(now, 2400))


def test_an_unknown_gap_is_not_reported_as_a_long_one() -> None:
    """The sentinel is 1e9 seconds. Printed, it would tell the model the
    room had been quiet for thirty years — which is worse than saying
    nothing, because it reads as a fact."""
    line = described(at("2026-08-18 14:00"))
    assert "тиша" not in line
    assert "дн" not in line


def test_unknown_does_not_pass_for_presence() -> None:
    """An engine that infers company from a failed query treats a fresh
    install as an invitation to talk."""
    blank = asyncio.run(observe(now=at("2026-08-18 14:00")))
    assert blank.silence_s == UNKNOWN_SILENCE_S
    assert blank.user_is_here is False


# ── the prompt says it once ───────────────────────────────────────────


def test_the_prompt_does_not_state_the_present_twice() -> None:
    """Two lines both opening "Зараз:" — one in words, one as a raw
    stamp — read as two claims about the same thing, and the raw one is
    the weaker: no weekday, and nothing about whether that hour is late.
    """
    from datetime import datetime

    from src.spine.prompt import build_system_prompt

    with_situation = build_system_prompt(
        persona="Ти heare.",
        situation_block="Зараз: вівторок, 01:08, ніч.",
        now=datetime(2026, 8, 18, 1, 8),
    )
    assert with_situation.count("Зараз:") == 1
    assert "2026-08-18" not in with_situation


def test_the_raw_stamp_survives_when_there_is_nothing_better() -> None:
    """With the engine switched off the timestamp is still the only thing
    telling it the time at all."""
    from datetime import datetime

    from src.spine.prompt import build_system_prompt

    bare = build_system_prompt(persona="Ти heare.", now=datetime(2026, 8, 18, 1, 8))
    assert "2026-08-18 01:08" in bare


# ── the two vocabularies ──────────────────────────────────────────────


def test_the_engine_understands_the_word_the_daemon_actually_writes() -> None:
    """`talking` is what mid-sentence looks like on this engine.

    The guard read listening / thinking / speaking — the old engine's
    words. The daemon has written idle / talking / interrupted since the
    spine replaced it, so the one condition standing between the engine
    and talking over you answered False through every sentence.
    """
    from src.spine.situation import Situation

    def at(phase: str) -> Situation:
        return Situation(
            now=0.0, hour=12, minute=0, weekday=1,
            silence_s=10.0, user_silence_s=10.0,
            bot_state=phase, bot_state_s=1.0, jobs_running=0,
            unprompted_last_s=1e9, unprompted_1h=0,
        )

    assert at("talking").busy_talking is True
    assert at("idle").busy_talking is False
    assert at("unknown").busy_talking is False


def test_no_phase_can_be_written_that_the_engine_has_not_heard_of() -> None:
    """A tripwire, not a style rule.

    An unknown phase does not raise and does not log: it falls through to
    "not busy", and the engine speaks into the middle of a sentence. So
    the daemon's own literals are read back out of the source, and a new
    one has to be classified here before the suite goes green again.
    """
    import re
    from pathlib import Path

    from src.spine.situation import BUSY_STATES, QUIET_STATES

    source = (
        Path(__file__).resolve().parent.parent
        / "src" / "daemon" / "spine_engine.py"
    ).read_text()
    written = set(re.findall(r"""_agent\(\s*["']([a-z_]+)["']""", source))

    assert written, "no _agent(...) calls found — did the writer move?"
    unknown = written - BUSY_STATES - QUIET_STATES
    assert not unknown, (
        f"the daemon writes {sorted(unknown)}, which the engine reads as "
        "idle — classify them in BUSY_STATES or QUIET_STATES"
    )


# ── presence ──────────────────────────────────────────────────────────


def _sit(**kw):
    from src.spine.situation import Situation

    base = dict(
        now=0.0, hour=12, minute=0, weekday=1,
        silence_s=10.0, user_silence_s=10.0,
        bot_state="idle", bot_state_s=1.0, jobs_running=0,
        unprompted_last_s=1e9, unprompted_1h=0,
    )
    base.update(kw)
    return Situation(**base)


def test_working_in_silence_is_not_an_empty_room() -> None:
    """The hour someone works without saying a word is the hour a
    proactive assistant exists for — and it was the hour the engine sat
    holding everything it had, because presence was measured by speech
    alone."""
    assert _sit(user_silence_s=3600.0, idle_s=5.0).user_is_here is True


def test_the_desk_can_be_empty_while_the_screen_is_on() -> None:
    assert _sit(user_silence_s=3600.0, idle_s=2400.0).user_is_here is False


def test_talking_is_still_being_here_when_the_keyboard_says_nothing() -> None:
    """Read aloud from across the room, hands nowhere near it."""
    from src.spine.situation import UNKNOWN_IDLE_S

    assert _sit(user_silence_s=30.0, idle_s=UNKNOWN_IDLE_S).user_is_here is True


def test_no_reading_is_not_a_person() -> None:
    """The sentinel must never be mistaken for someone sitting there: a
    machine with no sensor falls back to speech, not to presence."""
    assert _sit(user_silence_s=3600.0).user_is_here is False


def test_a_broken_sensor_leaves_the_engine_where_it_was() -> None:
    from src.spine.situation import UNKNOWN_IDLE_S, observe

    async def boom() -> float:
        raise OSError("ioreg is not a thing here")

    got = asyncio.run(observe(idle=boom, now=1000.0))
    assert got.idle_s == UNKNOWN_IDLE_S


def test_the_keyboard_reading_reaches_the_situation() -> None:
    from src.spine.situation import observe

    async def idle() -> float:
        return 12.0

    assert asyncio.run(observe(idle=idle, now=1000.0)).idle_s == 12.0
