"""Two lanes into the conversation — docs/archive/2026-09-16/design.html §6, the inbox.

What the person says interrupts what the loop is doing; what anything
else sends — a worker's result, a remark the engine wants to make —
waits for quiet. The turn being answered is a task now, so the next
thing the person says can end it instead of queuing behind it.

Ending is not killing. A reply that is cut still writes down what of it
was heard, so the next prompt shows the person their own last words
unanswered and the assistant's half-sentence as it was — which is what
lets «подивись, будь ласка… скільки місця на диску» be answered once,
as one question, rather than the first half getting a full reply.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

from src.mouth import Mouth
from tests.test_spine_loop import FakeAudio, _make_loop

pytestmark = pytest.mark.asyncio


class _Speaking(FakeAudio):
    """Sound comes out for as long as the reply is being made."""

    playing = property(lambda self: bool(self.busy))

    def __init__(self) -> None:
        super().__init__()
        self.busy = False


def _slow_loop(*, sentences_ms: float = 40.0):
    """A loop whose reply takes long enough to be interrupted: each
    delta sleeps, so a second turn can land mid-answer."""
    audio = _Speaking()
    asked: list[list[dict]] = []

    async def stream_chat(messages: list[dict]) -> AsyncIterator[str]:
        asked.append([dict(m) for m in messages])
        audio.busy = True
        try:
            for word in ("Перше", "речення", "довгої", "відповіді."):
                await asyncio.sleep(sentences_ms / 1000.0)
                yield word + " "
        finally:
            audio.busy = False

    loop = _make_loop(audio)
    loop.stream_chat = stream_chat
    loop.mouth = Mouth(audio)
    return loop, asked


async def test_the_person_interrupts_a_reply_in_progress() -> None:
    loop, asked = _slow_loop()
    loop.assembler._ready.append("подивись будь ласка")
    run = asyncio.create_task(loop.run())
    await asyncio.sleep(0.06)  # mid-reply: the first delta is out
    assert loop._turn is not None and not loop._turn.done()

    loop.assembler._ready.append("скільки місця на диску")
    await asyncio.sleep(0.4)
    run.cancel()
    await asyncio.gather(run, return_exceptions=True)

    assert len(asked) == 2, "the second question got its own answer"
    # Both halves are in the prompt of the second answer, the first one
    # with whatever half-reply it got — never silently merged, never lost.
    roles = [m["role"] for m in asked[1] if m["role"] != "system"]
    texts = [m["content"] for m in asked[1] if m["role"] == "user"]
    assert texts == ["подивись будь ласка", "скільки місця на диску"]
    assert roles.count("user") == 2
    assert loop.mouth.interrupted is False, "the second reply ran to its end"


async def test_a_workers_result_waits_for_the_reply_to_end() -> None:
    loop, asked = _slow_loop()
    loop.assembler._ready.append("розкажи щось")
    run = asyncio.create_task(loop.run())
    await asyncio.sleep(0.06)
    first = loop._turn

    await loop.inject("[результат роботи] сорок гігабайт", source="hands")
    await asyncio.sleep(0.05)
    assert loop._turn is first, "a result does not cut the person's answer short"
    assert not first.done()

    await asyncio.sleep(0.5)
    run.cancel()
    await asyncio.gather(run, return_exceptions=True)

    assert len(asked) == 2
    assert [m["content"] for m in asked[0] if m["role"] == "assistant"] == []
    assert "Перше речення довгої відповіді." in [
        m["content"] for m in asked[1] if m["role"] == "assistant"
    ], "the first reply was allowed to finish, and the result came after"


async def test_the_dashboard_is_the_person_too() -> None:
    loop, asked = _slow_loop()
    loop.assembler._ready.append("розкажи щось")
    run = asyncio.create_task(loop.run())
    await asyncio.sleep(0.06)

    await loop.inject("ні, краще інше", source="dashboard")
    await asyncio.sleep(0.4)
    run.cancel()
    await asyncio.gather(run, return_exceptions=True)

    assert len(asked) == 2
    assert [m["content"] for m in asked[1] if m["role"] == "user"][-1] == "ні, краще інше"


async def test_a_turn_that_will_not_end_is_cancelled_not_waited_on() -> None:
    """Stopped means stopped. A reply stuck somewhere below the model —
    a tool that never returns — is given a moment, then cut."""
    loop, asked = _slow_loop()

    async def never(messages):
        asked.append(messages)
        await asyncio.sleep(3600)
        yield ""

    loop.stream_chat = never
    loop.assembler._ready.append("зависни")
    run = asyncio.create_task(loop.run())
    await asyncio.sleep(0.05)
    stuck = loop._turn

    async def quick(messages):
        asked.append(messages)
        yield "Відповідаю."

    loop.stream_chat = quick
    loop.assembler._ready.append("а тепер відповідай")
    # _let_finish waits `patience` seconds; the test cannot wait five.
    original = loop._let_finish

    async def impatient(task, patience: float = 5.0):
        await original(task, patience=0.05)

    loop._let_finish = impatient
    await asyncio.sleep(0.5)
    run.cancel()
    await asyncio.gather(run, return_exceptions=True)

    assert stuck.cancelled() or stuck.done()
    assert len(asked) == 2, "the person got their answer"
