"""How the wake window actually closes.

These drive pipecat's own strategy rather than our wrapper, because what
was wrong was never in our code — it was in what we assumed the strategy
measured. Written after replaying three days of recordings and finding a
window that stayed open for four and a half minutes while a film played.

They are characterisation tests: they assert today's behaviour, including
the part of it we consider a defect. When the gate is replaced, these
fail, and that is the point — each one names a property the replacement
has to decide about on purpose.
"""

from __future__ import annotations

import asyncio

import pytest
from pipecat.frames.frames import TranscriptionFrame
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start.wake_phrase_user_turn_start_strategy import (
    WakePhraseUserTurnStartStrategy,
    _WakeState,
)
from pipecat.utils.asyncio.task_manager import TaskManager, TaskManagerParams

WINDOW = 0.4  # short enough to test, long enough not to race the loop


def said(text: str) -> TranscriptionFrame:
    return TranscriptionFrame(text=text, user_id="u", timestamp="0")


async def a_gate(*, window: float = WINDOW) -> WakePhraseUserTurnStartStrategy:
    manager = TaskManager()
    manager.setup(TaskManagerParams(loop=asyncio.get_running_loop()))
    gate = WakePhraseUserTurnStartStrategy(phrases=["дока", "докер"], timeout=window)
    await gate.setup(manager)
    return gate


async def test_the_window_measures_silence_in_the_room_not_conversation() -> None:
    """The defect, stated as a test.

    Any transcription refreshes the timeout — the room's as much as
    yours. So the noisier it is, the longer the assistant stays awake,
    which is exactly backwards: noise is when a false wake costs most.

    Live consequence, 12 August: one "Дока, привіт!" at 22:37:43 and a
    film playing held the window open until the daemon restarted at
    22:42:22. Twenty-eight of the utterances inside it were the film.
    """
    gate = await a_gate()
    await gate.process_frame(said("дока привіт"))
    assert gate.state is _WakeState.AWAKE

    for _ in range(4):  # four windows' worth of somebody else talking
        await asyncio.sleep(WINDOW / 2)
        await gate.process_frame(said("Дякую за перегляд! Фредди, ніфі мій."))

    assert gate.state is _WakeState.AWAKE, "noise no longer holds the window open"

    await asyncio.sleep(WINDOW * 2.5)  # the same span, actually silent
    assert gate.state is _WakeState.IDLE

    await gate.cleanup()


async def test_an_ordinary_word_that_contains_the_name_wakes_it() -> None:
    """Matching is exact-word over the transcript with no notion of being
    addressed, so any sentence carrying the token counts as a summons.

    "докер" is in the phrase list because Whisper writes it for "Дока".
    It is also a word this user says about containers.
    """
    gate = await a_gate()
    for unrelated in ("перше речення", "друге речення", "третє речення"):
        await gate.process_frame(said(unrelated))
    assert gate.state is _WakeState.IDLE

    await gate.process_frame(said("докер контейнер не піднявся"))
    assert gate.state is _WakeState.AWAKE

    await gate.cleanup()


async def test_the_name_does_not_assemble_from_fragments() -> None:
    """Accumulated text is capped at 250 characters and survives across
    utterances, which reads alarming — but each utterance is appended
    with a separating space, so "до" and "ка" become "до ка" and the
    word boundary never matches.

    Recorded because it was claimed as a defect and was not one. Every
    phrase we ship is a single word, so the accumulator changes nothing.
    """
    gate = await a_gate()
    await gate.process_frame(said("ми обговорювали до"))
    await gate.process_frame(said("ка це окрема тема"))

    assert gate.state is _WakeState.IDLE
    await gate.cleanup()


async def test_asleep_it_blocks_the_turn_and_not_the_hearing() -> None:
    """The gate returns STOP while idle, so no turn starts — but the
    transcription frame itself has already been produced and recorded
    upstream. That split is what makes "listen and remember, answer when
    asked" possible instead of a contradiction.
    """
    gate = await a_gate()

    assert await gate.process_frame(said("зовсім не про нього")) is (
        ProcessFrameResult.STOP
    )

    await gate.process_frame(said("дока"))
    assert await gate.process_frame(said("а тепер по суті")) is (
        ProcessFrameResult.CONTINUE
    )

    await gate.cleanup()


@pytest.mark.parametrize("event", ["on_wake_phrase_detected", "on_wake_phrase_timeout"])
async def test_both_transitions_are_observable(event: str) -> None:
    """pipecat logs each transition at DEBUG, which the daemon does not
    enable — so three days of logs held no record of a single wake, and
    every claim about when it was listening was reconstruction from
    whether it happened to reply.

    src/pipeline/build.py subscribes to both. This asserts the names it
    subscribes with still exist.
    """
    gate = await a_gate()
    seen: list = []

    @gate.event_handler(event)
    async def _record(_gate, *args) -> None:
        seen.append(args)

    await gate.process_frame(said("дока привіт"))
    await asyncio.sleep(WINDOW * 2.5)

    assert seen, f"{event} never fired"
    await gate.cleanup()
