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
