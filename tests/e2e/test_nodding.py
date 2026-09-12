"""Nodding at it, and talking over it: pause at once, decide later.

docs/design.html §6. On the sound of a voice the speaker goes quiet
within milliseconds; whether that was a nod («ага»), a command («стоп»)
or an interruption is known half a second later, from how long the
speech lasted and what it said. A nod costs a pause and the sentence
again from its start; anything else ends the utterance.

Both halves are wired: the mouth (pause, resume, stop, outcome) and the
ear's decision (src/text/backchannel.py, injected into the loop as its
judge). What the room cannot tell is how it sounds — that is the ear's.
"""
from __future__ import annotations

import pytest

from tests.e2e.room import close_room, open_room, real_speech

pytestmark = pytest.mark.asyncio


async def _speaking(room, said: str, *, ms: float = 800.0) -> None:
    """Get it talking, through the microphone, and wait until it is."""
    room.speaks_for(ms)
    room.will_say(said)
    await room.into_the_mic("Дока, розкажи щось довге")
    assert await room.until_speaking(), "it never got as far as speaking"


# ── holds today ────────────────────────────────────────────────────────


async def test_talking_over_it_ends_the_utterance_as_barged(tmp_path) -> None:
    """The one thing the mouth already knows how to say about itself."""
    room = await open_room(tmp_path)
    try:
        await _speaking(room, "Це довга відповідь, яку можна перебити.")
        reply = room.loop.mouth.last  # what was said over gets its own utterance next
        assert await room.cuts_in("ні, стоп, не про те", ms=800)
        assert reply.outcome == "barged"
    finally:
        await close_room(room)


# ── step 2: the ear decides ────────────────────────────────────────────


async def test_a_nod_pauses_and_the_reply_goes_on(tmp_path) -> None:
    """«ага», 400 ms — long enough for the detector to call it speech,
    short of the hold: the speaker pauses, the transcript says it was a
    nod, the sentence is said again from its start, the utterance ends
    «done» and nothing counts as interrupted. Today any speech stops it."""
    room = await open_room(tmp_path)
    try:
        await _speaking(room, "Перше речення відповіді. Друге речення відповіді.")
        reply = room.loop.mouth.last
        await room.nods("ага", ms=400)
        assert await room.finished(reply) == "done"
        assert not reply.stopped, "a nod is not an interruption"
        assert not room.loop.interrupted
    finally:
        await close_room(room)


async def test_a_short_stop_still_stops(tmp_path) -> None:
    """«стоп» is shorter than the hold, and still a command. It holds
    today because any speech stops it; it must keep holding once only
    the text decides."""
    room = await open_room(tmp_path)
    try:
        await _speaking(room, "Розповідаю довго й не про те.")
        reply = room.loop.mouth.last
        await room.nods("стоп", ms=400)
        assert reply.outcome in ("cancelled", "barged")
    finally:
        await close_room(room)


async def test_a_cough_is_waved_through(tmp_path) -> None:
    """A short sound that transcribes to nothing pauses and resumes; the
    reply is not lost to a cough."""
    room = await open_room(tmp_path)
    try:
        await _speaking(room, "Перше речення. Друге речення.")
        reply = room.loop.mouth.last
        await room.nods("", ms=400)
        assert await room.finished(reply) == "done"
    finally:
        await close_room(room)


# ── the number the design guesses ──────────────────────────────────────


@pytest.mark.spine_live
def test_how_long_a_nod_lasts() -> None:
    """Real speech through the real detector, so INTERRUPT_HOLD_MS is a
    measurement and not a guess. Prints a table; asserts only that nods
    are shorter than the hold."""
    from src.spine.vad import EnergyVAD
    from src.text.backchannel import INTERRUPT_HOLD_MS

    phrases = [("ага", "nod"), ("угу", "nod"), ("так-так", "nod"),
               ("стоп", "command"), ("далі", "command"), ("а скільки?", "question")]
    rows = []
    for phrase, kind in phrases:
        pcm = real_speech(phrase)
        vad = EnergyVAD()
        start = end = None
        frame = 640  # 20 ms at 16 kHz int16
        for at in range(0, len(pcm) - frame + 1, frame):
            event = vad.feed(pcm[at:at + frame])
            if event is None:
                continue
            kind_of = getattr(event.kind, "value", event.kind)
            ms = at / 32.0
            if kind_of == "start" and start is None:
                start = ms
            elif kind_of == "end":
                end = ms
        rows.append((phrase, kind, (end - start) if start is not None and end is not None else None))
    print("\nphrase        kind       ms")
    for phrase, kind, ms in rows:
        print(f"{phrase:<13} {kind:<10} {ms if ms is None else round(ms)}")
    nods = [ms for _, kind, ms in rows if kind == "nod" and ms is not None]
    assert nods, "the detector never saw a nod start and end"
    assert max(nods) < INTERRUPT_HOLD_MS, f"a nod lasted {max(nods):.0f} ms — over the hold"
