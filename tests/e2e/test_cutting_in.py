"""Talking over it.

The fastest thing the assistant does is stop. Everything else can be a
second late and merely feel slow; being unable to stop is the difference
between a machine you can talk to and one you have to wait out.

Two halves, and only one of them was ever tested. The half that *obeys*
a decision to stop — `_interrupted` set, the speaker starved, the text
still landing in history — has five unit tests, each of which sets the
flag by hand. The half that *makes* the decision — a voice arriving
while the speaker is live, the detector calling it speech, the queued
audio dropped, the canceller's reference thrown away with it — had none,
at any layer, because the mouth in this harness reported `playing =
False` forever and the conductor asks that question first.

What stood in for the missing coverage was a measurement in
`docs/findings/known-broken.md`: interruption lands "2 of 4, and the
canceller is involved". That number was taken in the pipecat engine,
whose file (`src/pipeline/stages/echo_classifier.py`) was deleted on 17
August. The spine has its own barge-in and its own canceller and has
never been measured at all. These scenarios are that measurement, and
the contract it should keep.
"""
from __future__ import annotations

import pytest

from tests.e2e.room import close_room, open_room, pack, voice_like

pytestmark = pytest.mark.asyncio


async def _speaking(room, said: str, *, ms: float = 800.0) -> None:
    """Get it talking, through the microphone, and wait until it is."""
    room.speaks_for(ms)
    room.will_say(said)
    await room.into_the_mic("Дока, розкажи щось довге")
    assert await room.until_speaking(), "it never got as far as speaking"


async def test_talking_over_it_stops_the_speaker(tmp_path) -> None:
    """The whole point, and the half that was unreachable until now.

    Note what is real here and was already real before this file existed:
    the energy detector, the WebRTC canceller (built, active, wired as
    the far-end sink), full duplex. Only the speaker was a stub. One
    hardcoded `False` is what kept this branch off every test path.
    """
    room = await open_room(tmp_path)
    try:
        await _speaking(room, "Це довга відповідь, яку можна перебити.")
        queued = room.mouth.queued_bytes()
        assert queued > 0, "the premise: there is audio still to come"

        assert await room.cuts_in("ні, стоп"), "it talked straight through"
        assert room.mouth.queued_bytes() == 0, "the speaker was not starved"
    finally:
        await close_room(room)


async def test_what_was_said_over_it_is_answered_not_only_obeyed(tmp_path) -> None:
    """Stopping is half of it. The other half is hearing.

    An assistant that goes quiet when talked over and then ignores what
    was said is worse than one that finishes its sentence: you interrupt
    to *change* something, and the correction landing nowhere is the
    failure the person actually notices.

    This crosses everything: detector, canceller, recogniser, the wake
    gate (open, because it was mid-conversation — and `_speak` extends
    an open window, so no name is needed a second time), the assembler
    and the conductor.
    """
    room = await open_room(tmp_path)
    try:
        room.speaks_for(800.0)
        room.will_say("Розповідаю дуже довго й не про те.", "Гаразд, зупиняюсь.")
        await room.into_the_mic("Дока, розкажи щось довге")
        assert await room.until_speaking()
        before = room.mark()

        assert await room.cuts_in("ні, стоп, не про це")

        # Two rows land after the interruption, in this order: the reply
        # that was cut off (recorded when its playback stops, which the
        # barge-in is what ends), then the answer to the correction.
        cut_off = await room.answer_after(before)
        answer = await room.answer_after(room.mark())

        assert cut_off == "Розповідаю дуже довго й не про те."
        assert "не про це" in room.heard()[-1], "the interruption was not written down"
        assert answer == "Гаразд, зупиняюсь.", "it never answered the correction"
    finally:
        await close_room(room)


async def test_the_canceller_forgets_audio_the_room_never_heard(tmp_path) -> None:
    """The conductor must reach for the speaker, not only for the flag.

    Be precise about what this holds. That the real audio layer drops the
    far-end reference along with the bytes is already pinned one layer
    down, in `test_spine_audio_io.py::test_stop_playback_clears_the_
    reference`; the mouth here only models it. What is untested anywhere
    else is that barge-in goes *through* `stop_playback()` at all — and a
    conductor that merely sets `_interrupted` looks correct from every
    other angle: it stops feeding new audio, the reply still lands in
    history, and 107 unit tests across the loop, the audio layer, the
    canceller and the detector stay green. What it does not do is starve
    the seconds already queued, or let go of the reference for them — so
    the canceller spends those seconds subtracting an echo the speaker
    never emitted, from the one sound in the room that has to survive
    intact: the voice that interrupted it.
    """
    room = await open_room(tmp_path)
    try:
        await _speaking(room, "Довга відповідь, яку зараз обірвуть.")
        assert room.mouth.cleared_far_end == 0

        assert await room.cuts_in("стоп")

        assert room.mouth.cleared_far_end == 1, (
            "the reference kept audio that never reached the room"
        )
    finally:
        await close_room(room)


async def test_with_the_switch_off_it_finishes_its_sentence(tmp_path) -> None:
    """Some rooms want that, and the dashboard offers it.

    The switch was decoration on the spine once already — pinned here
    from the detection side, where it is actually read.
    """
    room = await open_room(tmp_path)
    try:
        room.loop.barge_in_enabled = False
        await _speaking(room, "Довга відповідь, яку не перебити.", ms=400.0)

        assert not await room.cuts_in("стоп"), "it stopped when told not to"
    finally:
        await close_room(room)


async def test_without_the_canceller_it_cannot_be_interrupted_at_all(tmp_path) -> None:
    """The honest cost of `--without aec`, which the feature table states
    in the user's words: «мікрофон глухне, поки асистент говорить».

    Worth a scenario because the old measurement reads the other way
    round — it has echo cancellation *off* landing 4 of 4 — and that
    engine is gone. In the spine, no canceller means half duplex, which
    means the microphone is muted for the whole reply and there is no
    interrupting voice to detect. Not a regression: a different design,
    and one that must not be quietly rediscovered as a bug.
    """
    room = await open_room(tmp_path, without="aec")
    try:
        assert not room.loop._duplex, "the premise: half duplex"
        await _speaking(room, "Відповідь, яку ніхто не перерве.", ms=400.0)
        assert room.mouth.mute_input, "half duplex must mute the mic while speaking"

        assert not await room.cuts_in("стоп")
    finally:
        await close_room(room)


async def test_speaking_into_a_silent_room_is_not_an_interruption(tmp_path) -> None:
    """The flag is a latch, and a latch set when nothing is playing is a
    mute: it silences the *next* thing the assistant tries to say. That
    has happened before, from the other end — a service phrase died
    because an interrupt outlived the utterance it belonged to.

    Here it is guarded from the detection side: no speaker running, no
    interrupt, whatever the microphone hears.
    """
    room = await open_room(tmp_path)
    try:
        room.speaks_for(10.0)
        room.will_say("Слухаю.")
        await room.into_the_mic("Дока, скажи щось коротке")
        await room.drained()

        assert not room.mouth.playing, "the premise: nothing is being said"
        await room.into_the_mic("а тепер інше питання")

        assert not room.loop._interrupted, "silence was mistaken for being talked over"
    finally:
        await close_room(room)


async def test_it_lands_every_time_not_two_times_in_four(tmp_path) -> None:
    """The measurement, replacing one taken against a deleted engine.

    Six consecutive interruptions in one conversation. Six because the
    number being replaced came from four-run samples of what turned out
    to behave like a coin flip, and because the failure that produced it
    was described as the interrupting voice never being *recognised as a
    voice* — a detector-and-canceller question, which is exactly what
    this loop now runs for real.

    If this ever goes flaky, that is the finding: it is the one test here
    whose value is the count.
    """
    room = await open_room(tmp_path)
    try:
        room.speaks_for(600.0)
        room.will_say(*[f"Довга відповідь номер {n}, яку зараз обірвуть."
                        for n in range(1, 8)])
        await room.into_the_mic("Дока, розкажи щось довге")

        landed = 0
        for _ in range(6):
            if not await room.until_speaking():
                break
            if await room.cuts_in("ні, стоп, далі не треба"):
                landed += 1
            await room.drained()

        assert landed == 6, f"interrupting landed {landed} times in 6"
    finally:
        await close_room(room)


# -- the room, with the assistant's own voice coming back ---------------
#
# Everything above feeds the microphone a clean voice: the synthesiser in
# this harness renders silence by default, so the canceller's reference
# is silence and there is no echo to remove. That measures the wiring,
# which is what was missing — but it is not what the old finding blamed.
# It blamed the acoustics: "the interrupting voice is not being
# recognised as a voice at all", with the canceller in the frame.
#
# So the two below feed a real one, and they are a pair on purpose. Each
# alone can pass for the wrong reason — a canceller that removes
# everything keeps the room quiet, a canceller that removes nothing lets
# the person through — and only together do they say the canceller is
# doing the job rather than one of the two things either side of it.
#
# What returns to the microphone is the audio that actually went to the
# speaker, delayed onto the microphone's timeline, rather than an
# unrelated noise. That distinction is the experiment: handed an echo it
# cannot model, AEC3 leaves enough behind to trigger the detector every
# time, and the assistant interrupts itself.

_MIC_RATE = 16000
_FRAME = 320                  # samples in 20 ms
_ECHO_DELAY_MS = 30           # speaker → microphone; what SpineAEC assumes
_FRAMES = 60                  # 1.2 s of room


def _onto_the_mic_timeline(pcm_24k: bytes) -> list[float]:
    """What the speaker is emitting, resampled 24 kHz → 16 kHz."""
    import struct

    src = list(struct.unpack("<%dh" % (len(pcm_24k) // 2), pcm_24k))
    out = []
    for i in range(int(len(src) * 2 / 3)):
        at = i * 1.5
        lo = int(at)
        hi = min(lo + 1, len(src) - 1)
        f = at - lo
        out.append(src[lo] * (1 - f) + src[hi] * f)
    return out


async def _a_room_with_a_speaker_in_it(room) -> list[float]:
    """Get it talking aloud, and return the echo the microphone hears."""
    room.speaks_aloud()
    room.speaks_for(2000.0)
    room.will_say("Це довга відповідь, і в кімнаті є динамік.")
    await room.into_the_mic("Дока, розкажи щось довге")
    assert await room.until_speaking()

    emitted = _onto_the_mic_timeline(b"".join(room.mouth.played))
    assert any(abs(x) > 1000 for x in emitted), (
        "the premise: the speaker is emitting a voice, not silence"
    )
    lag = int(_MIC_RATE * _ECHO_DELAY_MS / 1000)
    return [emitted[i - lag] if lag <= i < len(emitted) + lag else 0.0
            for i in range(_FRAMES * _FRAME)]


async def test_it_does_not_interrupt_itself_when_it_hears_its_own_voice(
    tmp_path,
) -> None:
    """The failure that costs more than a missed interruption.

    An assistant that stops every time it hears itself cannot finish a
    sentence in a room with a speaker in it — and full duplex is exactly
    the arrangement that exposes it, because the microphone is left open
    for the whole reply on purpose.

    Sixty consecutive frames of nothing but the reply coming back, at
    full speaker level, and the detector must stay quiet through all of
    them. It is the canceller that makes this pass: stub `SpineAEC.
    process` out to return its frame unchanged and this goes red while
    everything else in the file stays green.
    """
    room = await open_room(tmp_path)
    try:
        echo = await _a_room_with_a_speaker_in_it(room)
        await room.frames(pack(echo))

        assert not room.loop._interrupted, "it stopped talking because of itself"
    finally:
        await close_room(room)


async def test_a_person_still_gets_through_over_that_echo(tmp_path) -> None:
    """The other side of the same room.

    Without this, the scenario above is satisfied by a canceller that
    deafens the microphone outright — which is what the old one turned
    out to be, twice, and it took an int16 overflow and four more bugs
    before anyone noticed the suppression was a mute.
    """
    room = await open_room(tmp_path)
    try:
        echo = await _a_room_with_a_speaker_in_it(room)
        room._next_heard = "ні, стоп"
        # A different voice for the person, so one filter cannot remove
        # them both, arriving a third of the way in.
        person = voice_like(_FRAMES * _FRAME, _MIC_RATE, 8000)
        heard = [echo[i] + (person[i] if i >= 20 * _FRAME else 0.0)
                 for i in range(_FRAMES * _FRAME)]
        await room.frames(pack(heard))

        assert room.loop._interrupted, "the person was lost under the echo"
    finally:
        await close_room(room)
