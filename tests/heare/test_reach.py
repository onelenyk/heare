"""An agent as an executor, against an agent that speaks the protocol and
does nothing. What is tested is everything between the core and the wire."""

from __future__ import annotations

import sys
from pathlib import Path

from heare.reach.agents import Agent, Known, installed
from tests.heare.fakes import open_room

FAKE = [sys.executable, str(Path(__file__).with_name("fake_agent.py"))]
KNOWN = Known("fake", sys.executable, tuple(FAKE), "нічого не робить")


async def _room(tmp_path, **kw):
    room = await open_room(**kw)
    agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None)
    room.core.add_executor(agent)
    return room, agent


async def _done(room, agent):
    await agent.close()
    await room.close()


async def test_a_task_goes_to_the_agent_and_its_answer_comes_back(tmp_path):
    room, agent = await _room(tmp_path)
    room.model.will_say("Готово.")
    task = room.core.delegate("зроби щось")
    await room.until(lambda: task.status == "done", timeout=10)
    assert task.result == "Зроблено."
    assert task.executor == "fake"
    await room.settled()
    assert room.page.said[-1] == "Готово. "
    await _done(room, agent)


async def test_a_change_is_asked_about_and_a_yes_lets_it_through(tmp_path):
    room, agent = await _room(tmp_path)
    task = room.core.delegate("WRITE the note")
    await room.until(lambda: room.core.state.permission is not None, timeout=10)
    assert room.core.state.permission.what == "змінити файл note.txt"
    room.says("так")
    await room.until(lambda: task.status == "done", timeout=10)
    assert task.result == "Записано."
    await _done(room, agent)


async def test_a_no_reaches_the_agent_as_a_refusal(tmp_path):
    room, agent = await _room(tmp_path)
    task = room.core.delegate("WRITE the note")
    await room.until(lambda: room.core.state.permission is not None, timeout=10)
    room.says("ні")
    await room.until(lambda: task.status == "done", timeout=10)
    # Said by the code, whatever the agent did or did not say.
    assert task.result.startswith("НЕ зроблено, бо людина сама щойно сказала «ні» на запит: змінити файл note.txt")
    await _done(room, agent)


async def test_looking_is_not_asked_about(tmp_path):
    room, agent = await _room(tmp_path)
    task = room.core.delegate("READ the note")
    await room.until(lambda: task.status == "done", timeout=10)
    assert task.result == "Записано."  # allowed without a question
    assert not any(e.kind == "permission" for e in room.core.state.journal)
    await _done(room, agent)


async def test_stop_reaches_the_agent(tmp_path):
    room, agent = await _room(tmp_path)
    task = room.core.delegate("HANG forever")
    await room.until(lambda: task.step == "Довга робота", timeout=10)
    room.says("Дока, стоп")
    await room.until(lambda: task.status == "stopped", timeout=10)
    await room.settled()
    assert room.page.said[-1] == "Зупинила."
    # The same process still answers: stopping one task is not killing the agent.
    room.model.will_say("Є.")
    again = room.core.delegate("ще одне")
    await room.until(lambda: again.status == "done", timeout=10)
    await _done(room, agent)


async def test_two_tasks_run_side_by_side_on_one_agent(tmp_path):
    room, agent = await _room(tmp_path)
    slow = room.core.delegate("HANG forever")
    await room.until(lambda: slow.step == "Довга робота", timeout=10)
    quick = room.core.delegate("щось швидке")
    await room.until(lambda: quick.status == "done", timeout=10)
    assert slow.status == "running"
    await _done(room, agent)


def test_only_agents_that_are_installed_are_offered():
    assert all(k.name in ("opencode", "claude") for k in installed())
