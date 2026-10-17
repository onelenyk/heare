"""The remote executor: work claimed over the wire, a result over the wire."""

from __future__ import annotations

import asyncio

from heare.reach.remote import RemoteExecutor


class Desk:
    def __init__(self) -> None:
        self.steps: list[str] = []

    def step(self, text: str) -> None:
        self.steps.append(text)

    async def permit(self, what: str, kind: str = "change") -> bool:
        return True


async def test_a_task_goes_out_and_a_result_comes_back():
    hands = RemoteExecutor("opencode", "код")
    desk = Desk()
    running = asyncio.create_task(hands.run("зроби сайт", desk))

    task = await hands.claim()
    assert task == {"id": task["id"], "request": "зроби сайт"}
    assert hands.step(task["id"], "працюю") is False
    assert desk.steps == ["працюю"]
    allow, stopped = await hands.permit(task["id"], "змінити файл", "change")
    assert allow is True and stopped is False
    assert hands.result(task["id"], "done", "Готово.") is True
    assert await asyncio.wait_for(running, 1.0) == "Готово."


async def test_after_a_stop_the_next_step_reads_as_stopped():
    hands = RemoteExecutor("opencode", "код")
    running = asyncio.create_task(hands.run("зроби", Desk()))
    task = await hands.claim()

    running.cancel()
    await asyncio.gather(running, return_exceptions=True)

    assert hands.step(task["id"], "працюю") is True  # gone: stopped
    allow, stopped = await hands.permit(task["id"], "змінити", "change")
    assert allow is False and stopped is True


async def test_claim_returns_nothing_when_there_is_no_work():
    hands = RemoteExecutor("opencode", "код", claim_wait_s=0.05)
    assert await hands.claim() is None
