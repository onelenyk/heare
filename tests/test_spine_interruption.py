"""Pause at once, decide later — the loop's half of docs/archive/2026-09-16/design.html §6.

A voice over the speaker used to mean stop, whatever the voice said. On
«ага» that cut every explanation off at the first nod. Now, with a judge
injected, the speaker pauses on the detector's START and the verdict
comes in one of two ways: the hold runs out while the person is still
talking (an interruption, decided before any text), or the transcript
arrives and the judge reads it — a nod resumes, a command stops, a cough
transcribed to nothing resumes.

Without a judge the loop behaves as it did: any voice stops it. That is
what every older barge-in test still checks.
"""
from __future__ import annotations

import asyncio

import pytest

from src.mouth import Mouth
from src.text.backchannel import decide
from tests.test_spine_loop import FakeAudio, FakeEvent, FakeKind, ScriptedVAD, _make_loop

pytestmark = pytest.mark.asyncio


class _Live:
    """A canceller that is on, so the loop runs full duplex."""

    active = True

    def process(self, frame: bytes) -> bytes:
        return frame


class _Playing(FakeAudio):
    """A speaker with sound still coming out of it."""

    @property
    def playing(self) -> bool:
        return True


def _start() -> FakeEvent:
    return FakeEvent(FakeKind("start"))


def _end() -> FakeEvent:
    return FakeEvent(FakeKind("end"), utterance=b"\x00" * 640)


async def _loop_with_judge(stt_text: str, *, hold_ms: float = 60.0, script: dict):
    """A loop mid-reply: one utterance open and sounding, a scripted ear."""
    audio = _Playing()
    loop = _make_loop(audio, vad=ScriptedVAD(script), stt_text=stt_text)
    loop.mouth = Mouth(audio)
    loop.aec = _Live()  # full duplex: the mic stays open while it speaks
    loop.judge_interruption = lambda text, ms: decide(text, speech_ms=ms)
    loop.interrupt_hold_ms = hold_ms
    utt = loop.mouth.open(loop.synthesise, mute_mic=False)
    await utt.say("Довге речення, яке хтось перебʼє.")
    return loop, utt


async def _feed(loop, frames: int) -> None:
    for _ in range(frames):
        loop.audio.input_frames.put_nowait(b"\x00" * 640)
    run = asyncio.create_task(loop.run())
    # Let the ear see every frame and the STT worker drain its queue.
    for _ in range(20):
        await asyncio.sleep(0.01)
        if loop._stt_jobs.empty() and loop.audio.input_frames.empty():
            break
    await asyncio.sleep(0.02)
    run.cancel()
    await asyncio.gather(run, return_exceptions=True)


async def test_a_nod_pauses_then_resumes_and_is_not_a_turn() -> None:
    loop, utt = await _loop_with_judge("ага", script={1: _start(), 2: _end()})
    await _feed(loop, 2)

    assert not utt.stopped, "a nod is not an interruption"
    assert not utt.paused, "and it does not leave the speaker paused"
    assert utt.outcome is None, "the reply is still going"
    # The assembler was told speech started; it is released with nothing,
    # so the nod never becomes a turn the model has to answer.
    assert loop.assembler.speech_starts == 1
    assert loop.assembler.fragments == [""]


async def test_a_stop_word_short_of_the_hold_still_stops() -> None:
    loop, utt = await _loop_with_judge("стоп", script={1: _start(), 2: _end()})
    await _feed(loop, 2)

    assert utt.stopped
    assert utt.outcome == "cancelled"
    assert loop.assembler.fragments == ["стоп"], "the command is a turn"


async def test_a_question_short_of_the_hold_interrupts_by_text() -> None:
    loop, utt = await _loop_with_judge("а скільки?", script={1: _start(), 2: _end()})
    await _feed(loop, 2)

    assert utt.stopped
    assert utt.outcome == "barged"
    assert loop.assembler.fragments == ["а скільки?"]


async def test_talking_past_the_hold_stays_paused_until_the_words_are_back() -> None:
    """START, then nothing — the person is still talking when the hold
    runs out. The speaker is silent already; whether the reply is over
    waits for the transcript, because a cough is loud and long too."""
    loop, utt = await _loop_with_judge("довга фраза", hold_ms=60.0, script={1: _start()})
    await _feed(loop, 4)  # 4 × 20 ms of audio, no END in sight

    assert utt.paused and not utt.stopped
    assert loop._hold is not None and loop._hold.long


async def test_after_the_hold_the_words_end_it_and_are_a_turn() -> None:
    """Long, and something was said: an interruption. The text is not
    thrown away with the pause — it is what the person wanted answered."""
    loop, utt = await _loop_with_judge(
        "розкажи натомість про інше", hold_ms=30.0, script={1: _start(), 6: _end()}
    )
    await _feed(loop, 6)

    assert utt.outcome == "barged"
    assert loop.assembler.fragments == ["розкажи натомість про інше"]
    assert loop._hold is None, "the hold is released with its verdict"


async def test_a_long_cough_resumes_once_it_transcribes_to_nothing() -> None:
    """The first live run cut a reply off on a cough that outlasted the
    hold. Long and empty is not an interruption."""
    loop, utt = await _loop_with_judge("", hold_ms=30.0, script={1: _start(), 6: _end()})
    await _feed(loop, 6)

    assert not utt.stopped and not utt.paused


async def test_a_nod_after_the_hold_does_not_resume() -> None:
    """Two seconds of talking that transcribe to «так» is not a nod; it
    is a person whose words the recogniser lost. Stop, and let the turn
    through."""
    loop, utt = await _loop_with_judge("так", hold_ms=30.0, script={1: _start(), 6: _end()})
    await _feed(loop, 6)

    assert utt.outcome == "barged"
    assert loop.assembler.fragments == ["так"]


async def test_a_cough_that_transcribes_to_nothing_resumes() -> None:
    loop, utt = await _loop_with_judge("", script={1: _start(), 2: _end()})
    await _feed(loop, 2)

    assert not utt.stopped and not utt.paused


async def test_stt_failing_while_on_hold_resumes_rather_than_stalls() -> None:
    loop, utt = await _loop_with_judge("ага", script={1: _start(), 2: _end()})

    async def broken(pcm: bytes):
        raise RuntimeError("stt down")

    loop.transcribe = broken
    await _feed(loop, 2)

    assert not utt.paused, "a pause nobody can resolve would mute it for good"
    assert not utt.stopped


async def test_without_a_judge_any_voice_stops_it() -> None:
    """The old contract, kept for whoever wires no judge."""
    audio = _Playing()
    loop = _make_loop(audio, vad=ScriptedVAD({1: _start()}))
    loop.mouth = Mouth(audio)
    loop.aec = _Live()
    utt = loop.mouth.open(loop.synthesise, mute_mic=False)
    await utt.say("Речення.")
    await _feed(loop, 1)

    assert utt.stopped and utt.outcome == "barged"
