"""It notices that it stopped hearing, and says so.

Four times in two days this project shipped something that looked alive
and was not: a build with no PortAudio, a process whose device vanished
when the machine slept, a bundle with no roles, a guard that could never
fire. Different bugs, one shape — plain from the inside, invisible from
the outside.

The watchdog closes the class rather than any one case, so what matters
here is that its verdict travels the same road as everything else: an
intent, judged, spoken once.
"""

from __future__ import annotations

import time

import pytest

from tests.e2e.room import close_room, open_room

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


@pytest.fixture
async def room(tmp_path):
    r = await open_room(tmp_path)
    try:
        yield r
    finally:
        await close_room(r)


class Ear:
    """An audio front end that can be broken on demand."""

    def __init__(self) -> None:
        self.input_open = True
        self.mute_input_user = False
        self._silent = 0.02

    def silent_for(self) -> float:
        return self._silent

    def goes_away(self) -> None:
        self._silent = 300.0

    def comes_back(self) -> None:
        self._silent = 0.02


async def hearing_of(room, ear: Ear):
    from src.spine.hearing import read

    async def _read():
        return read(ear)

    room.loop.engine._hearing = _read


async def test_a_working_ear_says_nothing(room) -> None:
    ear = Ear()
    await hearing_of(room, ear)

    await room.loop.engine._listen_to_itself(now=time.time())

    assert room.intents() == []


async def test_a_device_that_went_away_is_worth_saying(room) -> None:
    ear = Ear()
    await hearing_of(room, ear)
    ear.goes_away()

    await room.loop.engine._listen_to_itself(now=time.time())

    kinds = [kind for kind, _text, _u, _s in room.intents()]
    assert kinds == ["deaf"]


async def test_it_is_raised_as_the_persons_business_and_urgently(room) -> None:
    """It is a fault in the thing they are trying to use, not the
    engine's own idea — which is what lets it through the night filter.
    At 02:00 someone talking to a deaf assistant still deserves to know.
    """
    from src.spine import intents as I

    ear = Ear()
    await hearing_of(room, ear)
    ear.goes_away()

    await room.loop.engine._listen_to_itself(now=time.time())

    _kind, _text, urgency, _state = room.intents()[0]
    assert urgency >= 0.8
    pending = room.intents("pending")
    assert pending, "it has to survive to be spoken"
    assert I.USER


async def test_an_outage_of_an_hour_is_still_one_remark(room) -> None:
    """Thirty-five identical retries went into a log overnight. Thirty
    five remarks would be worse than none."""
    ear = Ear()
    await hearing_of(room, ear)
    ear.goes_away()

    now = time.time()
    for minute in range(0, 60, 5):
        await room.loop.engine._listen_to_itself(now=now + minute * 60)

    assert len(room.intents()) == 1


async def test_a_second_fault_is_said_again(room) -> None:
    """The first remark was about the first outage. Someone who fixed
    that one has no reason to assume the next."""
    ear = Ear()
    await hearing_of(room, ear)
    now = time.time()

    ear.goes_away()
    await room.loop.engine._listen_to_itself(now=now)
    ear.comes_back()
    await room.loop.engine._listen_to_itself(now=now + 600)
    ear.goes_away()
    await room.loop.engine._listen_to_itself(now=now + 1200)

    assert len(room.intents()) == 2


async def test_a_mute_is_not_a_fault(room) -> None:
    """Silence you asked for is not a failure, and an assistant that
    announces its own mute is worse than one that says nothing."""
    ear = Ear()
    ear.mute_input_user = True
    ear.goes_away()
    await hearing_of(room, ear)

    await room.loop.engine._listen_to_itself(now=time.time())

    assert room.intents() == []


async def test_what_it_says_is_a_sentence_a_person_can_act_on(room) -> None:
    ear = Ear()
    ear.input_open = False
    await hearing_of(room, ear)

    await room.loop.engine._listen_to_itself(now=time.time())

    _kind, text, _u, _s = room.intents()[0]
    assert "мікрофон" in text and "перезапустити" in text
    assert "stream" not in text.lower()
