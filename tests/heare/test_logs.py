"""A log line says when; a warning that repeats is written once and counted."""

from __future__ import annotations

import logging

from heare.logs import Once


def _record(msg, *args, level=logging.WARNING, name="heare.x"):
    return logging.LogRecord(name, level, __file__, 1, msg, args, None)


def test_a_repeated_warning_is_held_back_and_then_counted():
    now = [0.0]
    once = Once(quiet_s=300, clock=lambda: now[0])

    assert once.filter(_record("the brain did not take %s", "/a"))
    assert not once.filter(_record("the brain did not take %s", "/b"))
    assert not once.filter(_record("the brain did not take %s", "/c"))

    now[0] = 301
    again = _record("the brain did not take %s", "/d")
    assert once.filter(again)
    assert again.getMessage() == "the brain did not take /d (ще 2 раз за 5 хв)"


def test_other_warnings_and_plain_info_are_not_held():
    once = Once(clock=lambda: 0.0)
    assert once.filter(_record("one"))
    assert once.filter(_record("two"))
    assert once.filter(_record("one", name="heare.y"))
    assert once.filter(_record("said", level=logging.INFO))
    assert once.filter(_record("said", level=logging.INFO))
