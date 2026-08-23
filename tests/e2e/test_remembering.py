"""Asking it what was said, and what it does with a conversation that ends.

Every case here is a defect that shipped. They passed their unit tests
and failed the first time somebody spoke to the assembled thing — which
is the entire argument for this layer existing.
"""

from __future__ import annotations

import time

import pytest

from tests.e2e.room import Says, close_room, open_room

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]

HOUR = 3600.0


@pytest.fixture
async def room(tmp_path):
    r = await open_room(tmp_path)
    try:
        yield r
    finally:
        await close_room(r)


async def test_it_does_not_answer_a_question_with_the_question(room) -> None:
    """Observed live, twice, word for word:

        «що я казав про бекапи?»
        → «Сьогодні ти казав: Дока, що я казав про бекапи?»

    The turn is written to the database before the model is asked, so the
    search finds the question itself: freshest, shortest, therefore
    top-ranked. Nothing in the unit tests could see this, because
    nothing there wrote a turn down before searching.
    """
    room.remembers("по бекапах вирішили: беремо rsync на другий диск, щоночі",
                   days_ago=7)
    room.will_say(
        Says(text="Дай гляну.",
             calls=(("search_conversations", {"query": "бекапи"}),))
    )

    said = await room.told("Дока, що я казав про бекапи?")

    assert "rsync" in said, "the thing actually said must come back"
    assert "що я казав про бекапи" not in said, "it answered with the question"


async def test_asking_the_same_thing_twice_does_not_quote_the_first_asking(
    room,
) -> None:
    """The freshness bound catches the question being asked right now. It
    does not catch the same question from half an hour ago — past the
    bound, and a perfect match precisely because it is the same words."""
    room.remembers("по бекапах вирішили: беремо rsync на другий диск", days_ago=7)
    room.remembers("Дока, що я казав про бекапи?", days_ago=0.02)  # ~30 min

    room.will_say(
        Says(text="", calls=(("search_conversations", {"query": "бекапи"}),))
    )
    said = await room.told("Дока, що я казав про бекапи?")

    assert "rsync" in said
    assert "що я казав" not in said


async def test_a_conversation_ends_in_silence_and_leaves_a_summary(room) -> None:
    """Nothing closed a conversation for nine days: the code that did went
    with the engine deleted on 13 August, and one row held every turn
    since. A voice assistant has no hang-up, so silence is the boundary.
    """
    room.will_say("Записав.", "Ага.", "Зрозумів.", "Готово.")
    for line in ("Дока, реліз у понеділок", "перенесли на середу",
                 "ревʼю в четвер", "і бекапи через rsync"):
        await room.told(line)

    # Thirty-one minutes later, with the model handed a summary to write.
    room.will_say("Реліз перенесли з понеділка на середу, ревʼю в четвер.")
    await room.tick(now=time.time() + 31 * 60)

    conversations = room.conversations()
    assert len(conversations) == 1
    _id, _start, end_ts, summary = conversations[0]
    assert end_ts is not None, "silence has to end it"
    assert summary, "and it has to leave something behind"
    assert "реліз" in summary.lower()


async def test_the_end_is_the_last_thing_said(room) -> None:
    """Stamped with the moment a tick noticed, every gap would read as
    part of the conversation."""
    room.will_say("Так.", "Ага.", "Добре.", "Зрозумів.")
    for line in ("Дока, перше", "друге", "третє", "четверте"):
        await room.told(line)
    last_said = room.rows()[-1][0]

    room.will_say("Про дрібниці.")
    await room.tick(now=time.time() + 90 * 60)

    _id, _start, end_ts, _summary = room.conversations()[0]
    assert abs(end_ts - last_said) < 2.0


async def test_two_lines_are_not_worth_a_summary(room) -> None:
    """Asked anyway, the model writes a sentence rather than admit there
    was nothing there — and an invented summary poisons every search that
    reads it afterwards."""
    room.will_say("Привіт.")
    await room.told("Дока, привіт")

    room.will_say("ЦЕ НЕ МАЛО БУТИ НАПИСАНЕ")
    await room.tick(now=time.time() + 31 * 60)

    _id, _start, end_ts, summary = room.conversations()[0]
    assert end_ts is not None, "it still ends"
    assert not summary, "but there is nothing to say about it"
