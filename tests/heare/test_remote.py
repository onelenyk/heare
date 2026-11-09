"""The remote executor: work claimed over the wire, a result over the wire."""

from __future__ import annotations

import asyncio

from heare.core import Outcome
from heare.reach.remote import RemoteExecutor


class Desk:
    def __init__(self) -> None:
        self.steps: list[str] = []
        self.follows = False
        self.asked = ""

    def step(self, text: str) -> None:
        self.steps.append(text)

    async def permit(self, what: str, kind: str = "change", detail: str = "") -> bool:
        return True


async def test_a_task_goes_out_and_a_result_comes_back():
    hands = RemoteExecutor("opencode@bench", "opencode", "код")
    desk = Desk()
    running = asyncio.create_task(hands.run("зроби сайт", desk))

    task = await hands.claim()
    assert task == {"id": task["id"], "request": "зроби сайт", "continues": False,
                    "asked": ""}
    assert hands.step(task["id"], "працюю") is False
    assert desk.steps == ["працюю"]
    allow, stopped = await hands.permit(task["id"], "змінити файл", "change")
    assert allow is True and stopped is False
    assert hands.result(task["id"], "done", "Готово.") is True
    assert await asyncio.wait_for(running, 1.0) == "Готово."


async def test_work_that_goes_on_from_the_last_says_so_to_the_body():
    hands = RemoteExecutor("opencode@bench", "opencode", "код")
    desk = Desk()
    desk.follows = True
    running = asyncio.create_task(hands.run("видали його", desk))

    task = await hands.claim()

    assert task["continues"] is True
    hands.result(task["id"], "done", "Видалив.")
    await asyncio.wait_for(running, 1.0)


async def test_after_a_stop_the_next_step_reads_as_stopped():
    hands = RemoteExecutor("opencode@bench", "opencode", "код")
    running = asyncio.create_task(hands.run("зроби", Desk()))
    task = await hands.claim()

    running.cancel()
    await asyncio.gather(running, return_exceptions=True)

    assert hands.step(task["id"], "працюю") is True  # gone: stopped
    allow, stopped = await hands.permit(task["id"], "змінити", "change")
    assert allow is False and stopped is True


async def test_a_failed_hand_result_is_not_a_done_string():
    hands = RemoteExecutor("opencode@bench", "opencode", "код")
    running = asyncio.create_task(hands.run("зроби", Desk()))
    task = await hands.claim()

    assert hands.result(task["id"], "failed", "Не вийшло.") is True
    assert await asyncio.wait_for(running, 1.0) == Outcome("failed", "Не вийшло.")


async def test_a_stopped_hand_result_is_an_outcome():
    hands = RemoteExecutor("opencode@bench", "opencode", "код")
    running = asyncio.create_task(hands.run("зроби", Desk()))
    task = await hands.claim()

    assert hands.result(task["id"], "stopped", "Спинено.") is True
    assert await asyncio.wait_for(running, 1.0) == Outcome("stopped", "Спинено.")


async def test_claim_returns_nothing_when_there_is_no_work():
    hands = RemoteExecutor("opencode@bench", "opencode", "код", claim_wait_s=0.05)
    assert await hands.claim() is None


async def test_the_action_in_full_goes_to_the_desk_and_who_allowed_it_comes_back():
    hands = RemoteExecutor("opencode@bench", "opencode", "код")
    seen: list = []

    class Weighing(Desk):
        async def permit(self, what, kind="change", detail=""):
            seen.append((what, kind, detail))
            self.verdict = ("judge", "як просили")
            return True

    running = asyncio.create_task(hands.run("прибери", Weighing()))
    task = await hands.claim()

    allow, stopped = await hands.permit(task["id"], "trash a", "judge", '{"command": "trash a"}')
    assert (allow, stopped) == (True, False)
    assert seen == [("trash a", "judge", '{"command": "trash a"}')]
    assert hands.verdict(task["id"]) == ("judge", "як просили")
    assert hands.verdict(999) == ("person", "")
    hands.result(task["id"], "done", "Так.")
    await asyncio.wait_for(running, 1.0)


async def test_two_hands_of_one_channel_are_two_executors():
    from heare.core import Core
    from tests.heare.fakes import Script

    core = Core(Script())
    server = RemoteExecutor("native-hands@server", "native-hands", "швидкі", "server")
    mac = RemoteExecutor("native-hands@mac", "native-hands", "швидкі", "mac")

    core.add_executor(server)
    core.add_executor(mac)

    assert set(core.executors) == {"native-hands@server", "native-hands@mac"}
    assert {executor.name for executor in core.executors.values()} == {"native-hands"}
    assert {executor.machine for executor in core.executors.values()} == {"server", "mac"}


async def test_an_away_hand_fails_fast():
    from heare.core.i18n import _ as said_as

    hands = RemoteExecutor("opencode@bench", "opencode", "код", "bench",
                           claim_wait_s=0.05)
    hands.set_present(False)

    outcome = await hands.run("x", Desk())

    assert isinstance(outcome, Outcome)
    assert outcome.status == "failed"
    assert outcome.text == said_as(
        "The hands on {where} are not connected.").format(where="bench")
    assert await hands.claim() is None


async def test_a_stale_job_is_never_handed_out():
    hands = RemoteExecutor("opencode@bench", "opencode", "код", claim_wait_s=0.05)
    stale = asyncio.create_task(hands.run("old", Desk()))
    await asyncio.sleep(0)
    fresh = asyncio.create_task(hands.run("new", Desk()))
    await asyncio.sleep(0)

    stale.cancel()  # the core's timeout path: the future dies, the job stays put
    task = await hands.claim()

    assert task is not None and task["request"] == "new"
    fresh.cancel()
    await asyncio.gather(stale, fresh, return_exceptions=True)


async def test_a_job_survives_a_short_reconnect(monkeypatch):
    monkeypatch.setattr("heare.reach.remote.AWAY_GRACE_S", 0.3)
    hands = RemoteExecutor("opencode@bench", "opencode", "код", claim_wait_s=0.05)
    desk = Desk()
    running = asyncio.create_task(hands.run("зроби", desk))
    await asyncio.sleep(0)

    hands.set_present(False)
    hands.set_present(True)

    task = await hands.claim()
    assert task is not None and task["request"] == "зроби"
    assert hands.result(task["id"], "done", "Готово.") is True
    assert await asyncio.wait_for(running, 1.0) == "Готово."
