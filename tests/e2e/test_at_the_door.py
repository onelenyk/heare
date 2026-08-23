"""Before the loop: what is paid for, and what is thrown away.

Every other file here starts with text already in hand. That skips a
stretch of real code between the microphone and the assembler, and it
is not a small one — it decides whether Groq gets paid at all, and
whether what came back was ever speech.

Both halves have a history in this project. Dead air transcribed as
«Дякую за перегляд!» is a documented failure of this deployment, and it
does not merely waste money: it arrives as a sentence, becomes a turn,
and the assistant answers something nobody said. The filter that stops
it has a second rule that reads the loop's own clock — «дякую» right
after an answer is a person being polite, the same word into silence is
Whisper filling a gap — and a value read across two modules with a
default in the middle is exactly the shape that has already cost this
project a week.
"""

from __future__ import annotations

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


async def test_a_sentence_gets_through_and_becomes_a_turn(room) -> None:
    """The baseline. If this fails, nothing else in the file means
    anything: it says the door is a door and not a wall."""
    room.will_say("Тридцять секунд.")

    survived = await room.spoken("Дока, який у нас таймаут?")

    assert survived == "Дока, який у нас таймаут?"
    await room.drained()
    assert room.said() == "Тридцять секунд."


async def test_dead_air_is_never_sent_to_be_recognised(room) -> None:
    """A VAD utterance is mostly preroll and trailing silence. Sending
    that to Groq buys a hallucination and pays for the privilege."""
    survived = await room.spoken("Дякую за перегляд!", quiet=True)

    assert survived == ""
    assert room.recognised == []
    assert room.cost_events() == []
    assert room.heard() == []


async def test_a_word_shorter_than_a_word_is_not_sent_either(room) -> None:
    """The gate is measured in milliseconds of genuinely loud audio, not
    in length of the recording. A cough is loud and brief."""
    survived = await room.spoken("Дока", ms=120)

    assert survived == ""
    assert room.recognised == []


async def test_speech_that_is_paid_for_is_counted(room) -> None:
    """The other side of the same gate: when the call is made, it lands
    in the ledger. Cost per turn is the one characteristic of this
    assistant nobody has ever measured."""
    room.will_say("Добре.")

    await room.spoken("Дока, запиши це.", ms=1000)

    assert room.recognised == [32000]
    assert room.cost_events() == [("stt", 1.0)]


async def test_a_hallucination_on_dead_air_never_becomes_a_turn(room) -> None:
    """Loud enough to be recognised, and still not something a person
    said. This is the one that reaches the assistant as a sentence."""
    survived = await room.spoken("Дякую за перегляд!")

    assert survived == ""
    assert room.recognised != [], "the point is that it *was* recognised"
    assert room.heard() == []


async def test_a_caption_marker_is_not_speech(room) -> None:
    survived = await room.spoken("[музика]")

    assert survived == ""


async def test_a_phrase_whisper_glued_together_is_not_a_sentence(room) -> None:
    """Forty-five rows in the real database look like this. No person
    says fourteen characters without a space."""
    survived = await room.spoken("Будьласка,бро.")

    assert survived == ""


async def test_thanks_into_silence_is_whisper_filling_a_gap(room) -> None:
    survived = await room.spoken("дякую")

    assert survived == ""


async def test_thanks_right_after_an_answer_is_a_person_being_polite(room) -> None:
    """The wire this file exists for.

    The filter's courtesy rule asks the loop when it last spoke. The
    loop stamps that on itself; the root reads it through a default. If
    that wire is ever cut the read silently returns zero, the window
    never opens, and «дякую» is thrown away forever — with nothing in
    any log to say so, because throwing it away is also the correct
    behaviour half the time.
    """
    room.will_say("Тридцять секунд.")
    await room.spoken("Дока, який у нас таймаут?")
    await room.drained()

    survived = await room.spoken("дякую")

    assert survived == "дякую"
