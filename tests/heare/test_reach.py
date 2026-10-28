"""An agent as an executor, against an agent that speaks the protocol and
does nothing. What is tested is everything between the core and the wire."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from heare.reach import acp, agents
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
    assert task.result.startswith("NOT done: the person just said «no» to: змінити файл note.txt")
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
    assert room.page.said[-1] == "Зупинив."
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


class _Desk:
    def step(self, text: str) -> None:
        pass

    async def permit(self, what: str, kind: str = "change", detail: str = "") -> bool:
        return True


class _Session:
    id = "s1"
    status = "done"
    text = "Готово."

    def __init__(self) -> None:
        self.configured: list[tuple[str, str]] = []

    async def set(self, option: str, value: str) -> bool:
        self.configured.append((option, value))
        return True

    async def prompt(self, text: str) -> str:
        return self.text

    async def cancel(self) -> None:
        pass


class _Acp:
    def __init__(self, session: _Session) -> None:
        self._session = session

    async def session(self, on_event) -> _Session:
        return self._session


async def _allow(session, request) -> str:
    return "allow"


def _step(update: dict) -> str:
    """The event text one ACP update makes."""
    session = acp.Session(acp.Agent("opencode", on_permission=_allow), "s1")
    session._update(update)
    return session.events[-1].text


def test_a_tool_step_says_what_the_tool_is_doing():
    assert _step({"sessionUpdate": "tool_call", "title": "bash",
                  "rawInput": {"command": "ls ~/Downloads | wc -l"}}) \
        == "bash · ls ~/Downloads | wc -l"
    assert _step({"sessionUpdate": "tool_call", "title": "read",
                  "rawInput": {"file_path": "~/Downloads/a.txt"}}) \
        == "read · ~/Downloads/a.txt"
    assert _step({"sessionUpdate": "tool_call", "title": "bash"}) == "bash"


def test_a_long_tool_step_is_cut_to_one_hundred_and_twenty():
    text = _step({"sessionUpdate": "tool_call", "title": "bash",
                  "rawInput": {"command": "x" * 200}})
    assert text.endswith("…")
    assert len(text) == 120


async def test_the_model_is_named_on_the_session(monkeypatch, tmp_path):
    session = _Session()
    agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None,
                  model="deepseek/other")

    async def started() -> _Acp:
        return _Acp(session)

    monkeypatch.setattr(agent, "_started", started)
    await agent.run("зроби щось", _Desk())
    assert session.configured == [("model", "deepseek/other")]


async def test_without_a_model_the_default_is_still_named(monkeypatch, tmp_path):
    session = _Session()
    agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None)

    async def started() -> _Acp:
        return _Acp(session)

    monkeypatch.setattr(agent, "_started", started)
    await agent.run("зроби щось", _Desk())
    assert session.configured == [("model", agents.MODEL)]


async def test_an_env_overlay_keeps_the_opencode_permission_config(
    tmp_path, monkeypatch
):
    seen: dict = {}

    class Captured:
        def __init__(self, name, **kw):
            seen.update(kw)

        async def start(self) -> None:
            pass

    monkeypatch.setattr(agents.acp, "Agent", Captured)
    opencode = Known("opencode", "opencode", ("opencode", "acp"), "x")
    patch = {"provider": {"deepseek": {"options": {"baseURL": "https://g"}}}}
    agent = Agent(opencode, home=tmp_path, env={"CUSTOM": "1"},
                  config_patch=patch, mode="careful")
    await agent._started()

    env = seen["env"]
    assert env["CUSTOM"] == "1"
    config = json.loads(Path(env["OPENCODE_CONFIG"]).read_text(encoding="utf-8"))
    assert config["permission"]["edit"] == "ask"
    assert config["provider"]["deepseek"]["options"]["baseURL"] == "https://g"


async def test_a_claude_overlay_is_used_as_it_is(tmp_path, monkeypatch):
    seen: dict = {}

    class Captured:
        def __init__(self, name, **kw):
            seen.update(kw)

        async def start(self) -> None:
            pass

    monkeypatch.setattr(agents.acp, "Agent", Captured)
    claude = Known("claude", "claude", ("npx", "-y", "adapter"), "x")
    agent = Agent(claude, home=tmp_path, env={"ONLY": "1"})
    await agent._started()
    assert seen["env"] == {"ONLY": "1"}


def test_installed_is_built_from_the_core_table(monkeypatch):
    monkeypatch.setattr(agents.runner, "detect", lambda: [
        {"type": "opencode", "binary": "opencode", "path": "/x/opencode"},
    ])
    found = installed()
    assert [known.name for known in found] == ["opencode"]
    assert found[0].command == ("opencode", "acp")


class _Desk:
    follows = False

    def step(self, text: str) -> None:
        pass

    async def permit(self, what: str, kind: str = "change", detail: str = "") -> bool:
        return True


async def test_work_that_goes_on_keeps_its_session_and_new_work_starts_clean(tmp_path):
    agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None)
    await agent.run("знайди найбільший файл", _Desk())
    first = agent.last_session

    await agent.run("видали його", _Desk(), resume=first)
    assert agent.last_session == first

    await agent.run("список покупок", _Desk())
    assert agent.last_session != first
    await agent.close()



def _rule(config: dict, command: str) -> str:
    """What opencode would do with a command: the last matching pattern wins."""
    import fnmatch

    found = "ask"
    for pattern, rule in config["permission"]["bash"].items():
        if fnmatch.fnmatchcase(command, pattern):
            found = rule
    return found


def test_in_careful_and_normal_everything_but_looking_comes_to_be_weighed():
    for mode in ("careful", "normal"):
        config = agents._opencode_config(mode=mode)
        permission = config["permission"]
        assert permission["*"] == "ask"  # MCP tools too: macos-use, any other
        assert permission["edit"] == "ask"
        assert permission["read"] == permission["grep"] == permission["webfetch"] == "allow"
        assert permission["heare_ask"] == "allow"  # a question to the person already
        assert _rule(config, "ls -la") == "allow"
        assert _rule(config, "python3 script.py") == "ask"
        assert _rule(config, "trash old.pdf") == "ask"
        assert _rule(config, "git push origin main") == "ask"

    auto = agents._opencode_config(mode="auto")
    assert auto["permission"]["*"] == "allow"
    assert _rule(auto, "rm old.pdf") == "allow"

    for mode in agents.MODES:
        config = agents._opencode_config(mode=mode)
        assert _rule(config, "sudo rm -rf /") == "deny"
        assert _rule(config, "security find-generic-password -s heare") == "deny"
        assert _rule(config, "defaults write com.apple.dock x") == "deny"


async def test_who_weighs_an_action_follows_the_mode_and_sees_it_whole(tmp_path):
    for mode, kind in (("normal", "judge"), ("careful", "change")):
        agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None,
                      mode=mode)
        seen: list = []

        class Desk(_Desk):
            async def permit(self, what, kind="change", detail=""):
                seen.append((kind, json.loads(detail)))
                return True

        await agent.run("WRITE the note", Desk())
        assert seen == [(kind, {"kind": "edit", "title": "Записати файл",
                                "rawInput": {"file_path": "note.txt"}})]
        await agent.close()


async def test_in_auto_the_agent_is_never_stopped_to_ask(tmp_path):
    agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None,
                  mode="auto")
    asked: list[str] = []

    class Desk(_Desk):
        async def permit(self, what: str, kind: str = "change", detail: str = "") -> bool:
            asked.append(what)
            return False

    result = await agent.run("WRITE the note", Desk())
    assert asked == [] and "NOT done" not in result
    await agent.close()


def test_an_unknown_mode_falls_back_to_normal(tmp_path):
    assert Agent(KNOWN, home=tmp_path, mode="reckless").mode == "normal"
