"""An agent as an executor, against an agent that speaks the protocol and
does nothing. What is tested is everything between the core and the wire."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

import pytest

from heare.reach import acp, agents, runner
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


def test_a_command_is_said_without_its_environment():
    said = agents._what({"toolCall": {
        "kind": "execute",
        "rawInput": {"command": "export CI=true GIT_PAGER=cat ls ~/Downloads | wc -l"},
    }})
    assert "ls ~/Downloads" in said
    assert "CI=true" not in said


def test_a_long_command_is_cut_for_the_voice():
    said = agents._what({"toolCall": {"kind": "execute",
                                      "rawInput": {"command": "x" * 300}}})
    doing = agents._(agents.N_("run the command"))
    assert said.endswith("…")
    assert said.startswith(doing + " ")
    assert len(said) - len(doing) - 1 <= 60


def test_a_file_is_named_without_its_folders():
    said = agents._what({"toolCall": {
        "kind": "edit", "rawInput": {"file_path": "/Users/x/Documents/resume.pdf"},
    }})
    assert "resume.pdf" in said
    assert "/Users" not in said


def test_a_command_of_assignments_only_is_still_said():
    said = agents._what({"toolCall": {"kind": "execute",
                                      "rawInput": {"command": "A=1 B=2"}}})
    assert "A=1 B=2" in said
    long_said = agents._what({"toolCall": {
        "kind": "execute", "rawInput": {"command": f"A={'.' * 300}"},
    }})
    assert long_said.endswith("…")


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

    # `auto` decides in `_decide`, not in the config: the wire must ask so
    # the never-floor sees every command, and `_decide` then says allow.
    auto = agents._opencode_config(mode="auto")
    assert auto["permission"]["*"] == "ask"
    assert _rule(auto, "rm old.pdf") == "ask"

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


async def test_agent_env_contains_only_the_chosen_key(tmp_path, monkeypatch):
    work = tmp_path / "heare-work"
    report = tmp_path / "report.json"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "chosen-secret")
    monkeypatch.setenv("GROQ_API_KEY", "unrelated-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "cloud-secret")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/ssh-agent")
    monkeypatch.setenv("GITHUB_TOKEN", "a-token")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    env = runner.env("opencode", "key", "DEEPSEEK_API_KEY", "", "")
    agent = acp.Agent("fake", on_permission=_allow, cwd=work, command=FAKE, env=env)
    await agent.start()
    session = await agent.session()
    await session.prompt(f"REPORT {report}")
    await agent.close()

    seen = json.loads(report.read_text(encoding="utf-8"))
    assert seen["env"]["DEEPSEEK_API_KEY"] == "chosen-secret"
    for secret in ("GROQ_API_KEY", "AWS_SECRET_ACCESS_KEY",
                   "SSH_AUTH_SOCK", "GITHUB_TOKEN"):
        assert secret not in seen["env"]
    assert "PATH" in seen["env"] and "HOME" in seen["env"]
    assert Path(seen["cwd"]) == work


async def test_claude_in_auto_still_denies_the_never_list(tmp_path):
    claude = Known("claude", "claude", ("claude", "acp"), "x")
    agent = Agent(claude, cwd=tmp_path, home=tmp_path, command=FAKE, env=None,
                  mode="auto")
    session = acp.Session(acp.Agent("claude", on_permission=_allow), "s1")
    asked: list[str] = []

    class Desk(_Desk):
        async def permit(self, what: str, kind: str = "change", detail: str = "") -> bool:
            asked.append(what)
            return True

    agent._desks[session.id] = Desk()
    for command in (
        "sudo rm -rf /",
        "/usr/bin/sudo id",
        "security find-generic-password -s heare",
        "/usr/bin/security find-internet-password",
        "defaults write com.apple.dock autohide -bool true",
        "bash -c 'sudo id'",
        "env sudo id",
        "xargs sudo id",
    ):
        verdict = await agent._decide(session, {"toolCall": {
            "kind": "execute", "title": "bash", "rawInput": {"command": command}}})
        assert verdict == "reject", command

    assert asked == []
    plain = await agent._decide(session, {"toolCall": {
        "kind": "execute", "title": "bash", "rawInput": {"command": "ls -la"}}})
    assert plain == "allow"


async def test_a_killed_agent_is_detected_and_restarted(tmp_path):
    agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None)

    with pytest.raises(acp.AcpError):
        await agent.run("DIE now", _Desk())
    first = agent._agent
    assert first is not None and not first.alive

    assert await agent.run("зроби щось", _Desk()) == "Зроблено."
    assert agent._agent is not None and agent._agent is not first
    await agent.close()


async def test_stop_cancels_a_running_command(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "STOP_GRACE_S", 0.2)
    monkeypatch.setattr(agents, "STOP_POLL_S", 0.02)
    sent: list[dict] = []
    killed: list[int] = []
    real_killpg = os.killpg

    def recording_killpg(pid: int, sig: int) -> None:
        killed.append(sig)
        real_killpg(pid, sig)

    monkeypatch.setattr(acp.os, "killpg", recording_killpg)
    agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None,
                  tap=lambda direction, msg: sent.append(msg)
                  if direction == "out" else None)

    class Desk(_Desk):
        stopped = False

        def step(self, text: str) -> None:
            self.stopped = True

    result = await asyncio.wait_for(agent.run("DEAF forever", Desk()), 5)

    assert result == "Stopped."
    assert any(m.get("method") == "session/cancel" for m in sent)
    assert signal.SIGTERM in killed
    await agent.close()


async def test_a_malformed_acp_frame_does_not_hang_the_turn(tmp_path):
    agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None)

    result = await asyncio.wait_for(agent.run("MALFORMED now", _Desk()), 5)

    assert result == "Зроблено."
    await agent.close()


async def test_a_rerun_of_the_harness_does_not_leak_a_child_process(tmp_path):
    agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None)
    assert await agent.run("зроби щось", _Desk()) == "Зроблено."
    inner = agent._agent
    assert inner is not None
    proc = inner._proc

    await agent.close()

    assert agent._agent is None
    assert proc is not None and proc.returncode is not None
    assert inner._reader is None
    assert await agent.run("зроби ще", _Desk()) == "Зроблено."
    second = agent._agent
    assert second is not None and second._proc is not None
    second_proc = second._proc
    await agent.close()
    assert second_proc.returncode is not None
