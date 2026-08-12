"""The turn ends when the words stop, not when the room does.

The failure this closes: "Дока, привіт. Скажи одним реченням, як ти себе
почуваєш", said in one breath, was greeted twice and then answered.
Recognition returns per segment, so one breath arrived as four
transcripts spread over five seconds, and the turn had already closed by
the second one.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from src.config import Settings
from src.pipeline.turns import sentence_turn_stop_strategy


@dataclass
class FakeTranscript:
    text: str
    finalized: bool = False


class FakeTasks:
    """Enough of pipecat's task manager to run the strategy."""

    def __init__(self) -> None:
        self.created: list[asyncio.Task] = []

    def create_task(self, coro, name: str = ""):
        task = asyncio.ensure_future(coro)
        self.created.append(task)
        return task

    async def cancel_task(self, task, timeout: float | None = None) -> None:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def _strategy(timeout: float = 0.2):
    strategy = sentence_turn_stop_strategy(user_speech_timeout=timeout)
    strategy._task_manager = FakeTasks()
    strategy._stopped = 0

    async def record():
        strategy._stopped += 1

    strategy.trigger_user_turn_stopped = record
    return strategy


@pytest.mark.asyncio
async def test_a_late_second_half_keeps_the_turn_open() -> None:
    """Two transcripts, the second arriving after the first would have
    ended the turn. One turn, not two."""
    s = _strategy(timeout=0.2)
    s._vad_user_speaking = False

    await s._handle_transcription(FakeTranscript("Дока, привіт."))
    await asyncio.sleep(0.12)
    assert s._stopped == 0, "ended the turn before the sentence was over"

    await s._handle_transcription(FakeTranscript(" Як ти себе почуваєш?"))
    await asyncio.sleep(0.12)
    assert s._stopped == 0, "the second half did not push the deadline back"

    await asyncio.sleep(0.2)
    assert s._stopped == 1
    assert "Як ти себе почуваєш?" in s._text
    assert "Дока" in s._text, "the first half was dropped from the turn"


@pytest.mark.asyncio
async def test_the_turn_does_end() -> None:
    """A deadline that is only ever pushed back is a mute assistant —
    which is exactly what the previous attempt at this shipped."""
    s = _strategy(timeout=0.15)
    s._vad_user_speaking = False

    await s._handle_transcription(FakeTranscript("привіт"))
    await asyncio.sleep(0.4)

    assert s._stopped == 1


@pytest.mark.asyncio
async def test_nothing_is_scheduled_while_the_person_is_still_speaking() -> None:
    """There is no deadline during speech: the words are just collected.
    The countdown belongs to the silence that follows."""
    s = _strategy(timeout=0.15)
    s._vad_user_speaking = True

    await s._handle_transcription(FakeTranscript("Дока,"))
    await asyncio.sleep(0.3)

    assert s._stopped == 0
    assert s._timeout_task is None
    assert s._text == "Дока,"


@pytest.mark.asyncio
async def test_the_words_accumulate_into_one_turn() -> None:
    """Four fragments, one question. The model must see the question."""
    s = _strategy(timeout=0.2)
    s._vad_user_speaking = True
    for piece in ("Дока.", " Привіт.", " Скажи однім реченням,"):
        await s._handle_transcription(FakeTranscript(piece))

    s._vad_user_speaking = False
    await s._handle_transcription(FakeTranscript(" Як ти себе почуваєш?"))
    await asyncio.sleep(0.35)

    assert s._stopped == 1
    assert s._text == "Дока. Привіт. Скажи однім реченням, Як ти себе почуваєш?"


# ── the setting ───────────────────────────────────────────────────────


def test_it_is_the_default() -> None:
    assert Settings().turn_end == "sentence"


def test_the_wait_after_the_last_word_stays_short() -> None:
    """With the two timers collapsed into one, this is the only latency
    dial left in the turn — and it is paid on every single reply."""
    assert 0.4 <= Settings().turn_silence_seconds <= 1.5
