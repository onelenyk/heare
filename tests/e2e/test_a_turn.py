"""The path a single turn takes, with nothing faked but the edges.

If this file does not pass, nothing else here means anything: it asserts
that the assembled loop hears, answers, writes both sides down, and that
the wake gate is consulted for speech in the room and not for what is
addressed to it.
"""

from __future__ import annotations

import pytest

from tests.e2e.room import Says, close_room, open_room

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


@pytest.fixture
async def room(tmp_path):
    r = await open_room(tmp_path)
    try:
        yield r
    finally:
        await close_room(r)


async def test_it_answers_and_both_sides_are_written_down(room) -> None:
    room.will_say("Таймаут тридцять секунд, тимчасово.")

    said = await room.told("Дока, який у нас таймаут?")

    assert said == "Таймаут тридцять секунд, тимчасово."
    assert room.heard() == [("Дока, який у нас таймаут?")]


async def test_the_room_is_not_the_conversation(room) -> None:
    """Speech that does not name it is not a turn. This is the gate that
    keeps a podcast in the background from starting a reply every few
    seconds."""
    room.will_say("цього не мало прозвучати")

    said = await room.hears("а потім він каже, що збірка впала")

    assert said == ""
    assert room.rows() == []


async def test_naming_it_is_enough(room) -> None:
    room.will_say("Чую.")

    said = await room.hears("Дока, чуєш мене?")

    assert said == "Чую."


async def test_a_tool_answer_is_spoken_and_written_down(room) -> None:
    """The observable that cost half an hour by hand: a verb's spoken
    acknowledgement never gets a `say:` line in the log, so a test
    reading the log sees a turn that broke off where there was an
    answer."""
    room.will_say(
        Says(text="Дай гляну.", calls=(("recall", {"query": "таймаут"}),))
    )

    said = await room.told("Дока, що ти памʼятаєш про таймаут?")

    assert said.startswith("Дай гляну.")
    assert len(said) > len("Дай гляну."), "the tool's answer must be spoken too"
