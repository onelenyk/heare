"""The way out: which voice reads the answer, and what that costs.

Picking the wrong voice is not cosmetic and not loud — Edge TTS answers
a Cyrillic sentence on an English voice with `NoAudioReceived`, which
arrives as silence. The assistant believes it spoke, the row is written,
the log says `say:`, and nobody in the room heard anything. That is the
worst class of defect this project has: correct from the inside,
missing from the outside.

The choice is made per sentence, not per reply, which is right for an
answer that switches language mid-way and is the reason these cases are
worth pinning: an assistant working in Ukrainian says English words all
day long.
"""

from __future__ import annotations

import pytest

from tests.e2e.room import close_room, open_room

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]

UK = "uk-UA"
EN = "en-US"


@pytest.fixture
async def room(tmp_path):
    r = await open_room(tmp_path)
    try:
        yield r
    finally:
        await close_room(r)


def voices(room) -> list[str]:
    """The voice each sentence was read by. Never empty: an answer that
    was never voiced at all is the same silence by another route, and
    `all()` over nothing is true."""
    chosen = [voice for _text, voice in room.voiced]
    assert chosen, "nothing was voiced"
    return chosen


async def test_a_ukrainian_answer_is_read_by_a_ukrainian_voice(room) -> None:
    room.will_say("Таймаут тридцять секунд.")

    await room.told("Дока, який таймаут?")

    assert all(v.startswith(UK) for v in voices(room)), voices(room)


async def test_an_english_answer_is_read_by_an_english_voice(room) -> None:
    room.will_say("The timeout is thirty seconds.")

    await room.told("Дока, what is the timeout?")

    assert all(v.startswith(EN) for v in voices(room)), voices(room)


async def test_english_words_inside_a_ukrainian_sentence_do_not_silence_it(
    room,
) -> None:
    """The everyday case, and the one that would be silence. Half the
    nouns in this project are Latin; the sentence is still Ukrainian and
    still has to be read by a voice that can pronounce Cyrillic."""
    room.will_say("Запусти docker compose up у теці infra.")

    await room.told("Дока, як підняти оточення?")

    assert all(v.startswith(UK) for v in voices(room)), voices(room)


async def test_each_sentence_is_voiced_for_itself(room) -> None:
    """An English aside inside a Ukrainian answer gets the English voice,
    and the Ukrainian around it does not."""
    room.will_say("Таймаут тридцять секунд. The default is sixty.")

    await room.told("Дока, який таймаут?")

    said = dict((text, voice) for text, voice in room.voiced)
    assert len(said) == 2, room.voiced
    for text, voice in said.items():
        expected = EN if text.strip().startswith("The") else UK
        assert voice.startswith(expected), (text, voice)


async def test_an_answer_with_no_letters_falls_back_to_ukrainian(room) -> None:
    """`pick_voice` has to decide something for «30.» — and English is
    the one answer that renders as nothing if the fallback is wrong."""
    room.will_say("30.")

    await room.told("Дока, скільки?")

    assert all(v.startswith(UK) for v in voices(room)), voices(room)


async def test_what_is_spoken_is_what_was_written_down(room) -> None:
    """The mouth and the database must agree. A tool acknowledgement is
    spoken outside the model flow, and for months this layer had no way
    to notice if one of the two paths dropped it."""
    room.will_say("Таймаут тридцять секунд.")

    said = await room.told("Дока, який таймаут?")

    assert "".join(text for text, _v in room.voiced).strip() == said.strip()
