"""What is said near it, kept or not kept, and erased on request.

The microphone already hears the room and Whisper already transcribes
all of it — the wake gate decides whether to *act*, not whether to
listen. So the decision was never "listen more", only "keep what was
already heard and paid for". These are the three conditions that made
keeping it survivable.
"""

from __future__ import annotations

import pytest

from tests.e2e.room import Says, close_room, open_room

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


@pytest.fixture
async def quiet(tmp_path):
    """Default: it hears the room and keeps nothing."""
    r = await open_room(tmp_path)
    try:
        yield r
    finally:
        await close_room(r)


@pytest.fixture
async def listening(tmp_path):
    r = await open_room(tmp_path, features={"hear_all": True})
    try:
        yield r
    finally:
        await close_room(r)


async def test_by_default_the_room_leaves_nothing(quiet) -> None:
    """A microphone is in a room, and the other people in it did not
    choose this. Off is what a default may be."""
    await quiet.overhears("колега сказав що реліз переносять на четвер")

    assert quiet.rows() == []


async def test_switched_on_it_is_kept_and_marked(listening) -> None:
    """Kept apart rather than mixed in: one is a conversation and stays,
    the other is the room and expires."""
    await listening.overhears("колега сказав що реліз переносять на четвер")

    rows = listening.rows()
    assert len(rows) == 1
    _ts, agent, text, source = rows[0]
    assert source == "overheard"
    assert agent == 0
    assert "реліз" in text


async def test_the_room_never_becomes_a_turn(listening) -> None:
    """Nothing answered it. Given a turn id it would join an exchange it
    was never part of, and read back as something said to the assistant.
    """
    import sqlite3

    await listening.overhears("хтось у кімнаті щось сказав")

    with sqlite3.connect(listening.db) as db:
        turns = db.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        turn_id = db.execute(
            "SELECT turn_id FROM transcripts WHERE source = 'overheard'"
        ).fetchone()[0]
    assert turns == 0
    assert turn_id is None


async def test_the_search_does_not_answer_with_the_room(listening) -> None:
    """A reader that does not ask for the room does not see it."""
    await listening.overhears("хтось згадав слово криптовалюта")
    listening.remembers("а по бекапах беремо rsync на другий диск", days_ago=3)

    listening.will_say(
        Says(calls=(("search_conversations", {"query": "криптовалюта"}),))
    )
    said = await listening.told("Дока, що я казав про криптовалюту?")

    assert "не пригадую" in said.lower()


async def test_forget_erases_the_room_and_leaves_the_conversation(
    listening,
) -> None:
    """A person has to be able to say "forget that" out loud and have it
    be true — without opening a database, and while whoever said it is
    still in the room."""
    await listening.overhears("те, чого не мало лишитись")

    listening.will_say("Записав.")
    await listening.told("Дока, реліз у середу")
    await listening.drained()

    listening.will_say(Says(calls=(("forget", {"minutes": 60}),)))
    said = await listening.told("Дока, забудь останню годину")

    assert "забула" in said.lower()
    assert listening.rows("source = 'overheard'") == []
    assert any("реліз" in text for text in listening.heard()), (
        "erasing what someone said *to* it is a different act entirely"
    )
