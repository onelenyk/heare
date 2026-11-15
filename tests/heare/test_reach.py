"""An agent as an executor, against a server that speaks the protocol and
does nothing. What is tested is everything between the core and the wire."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

import pytest

from heare.reach import agents, opencode, runner
from heare.reach.agents import Agent, Known, installed
from tests.heare.fakes import open_room

FAKE = [sys.executable, str(Path(__file__).with_name("fake_opencode.py"))]
# Every task here starts a real server in a child process. A loaded runner
# makes that slow; the wait must be about correctness, not about speed.
WAIT_S = 60.0
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
    await room.until(lambda: task.status == "done", timeout=WAIT_S)
    assert task.result == "Зроблено."
    assert task.executor == "fake"
    await room.settled()
    assert room.page.said[-1] == "Готово. "
    await _done(room, agent)


async def test_a_change_is_asked_about_and_a_yes_lets_it_through(tmp_path):
    room, agent = await _room(tmp_path)
    task = room.core.delegate("WRITE the note")
    await room.until(lambda: room.core.state.permission is not None, timeout=WAIT_S)
    assert room.core.state.permission.what == "змінити файл note.txt"
    room.says("так")
    await room.until(lambda: task.status == "done", timeout=WAIT_S)
    assert task.result == "Записано."
    await _done(room, agent)


async def test_a_no_reaches_the_agent_as_a_refusal(tmp_path):
    room, agent = await _room(tmp_path)
    task = room.core.delegate("WRITE the note")
    await room.until(lambda: room.core.state.permission is not None, timeout=WAIT_S)
    room.says("ні")
    await room.until(lambda: task.status == "done", timeout=WAIT_S)
    # Said by the code, whatever the agent did or did not say.
    assert task.result.startswith("NOT done: the person just said «no» to: змінити файл note.txt")
    await _done(room, agent)


async def test_looking_is_not_asked_about(tmp_path):
    room, agent = await _room(tmp_path)
    task = room.core.delegate("READ the note")
    await room.until(lambda: task.status == "done", timeout=WAIT_S)
    assert task.result == "Записано."  # allowed without a question
    assert not any(e.kind == "permission" for e in room.core.state.journal)
    await _done(room, agent)


async def test_stop_reaches_the_agent(tmp_path):
    room, agent = await _room(tmp_path)
    task = room.core.delegate("HANG forever")
    await room.until(lambda: task.step and "bash" in task.step, timeout=WAIT_S)
    room.says("Дока, стоп")
    await room.until(lambda: task.status == "stopped", timeout=WAIT_S)
    await room.settled()
    assert room.page.said[-1] == "Зупинив."
    # The same server still answers: stopping one task is not killing it.
    room.model.will_say("Є.")
    again = room.core.delegate("ще одне")
    await room.until(lambda: again.status == "done", timeout=WAIT_S)
    await _done(room, agent)


async def test_two_tasks_run_side_by_side_on_one_agent(tmp_path):
    room, agent = await _room(tmp_path)
    slow = room.core.delegate("HANG forever")
    await room.until(lambda: slow.step and "bash" in slow.step, timeout=WAIT_S)
    quick = room.core.delegate("щось швидке")
    await room.until(lambda: quick.status == "done", timeout=WAIT_S)
    assert slow.status == "running"
    await _done(room, agent)


def test_only_agents_that_are_installed_are_offered():
    assert all(k.name == "opencode" for k in installed())


class _Desk:
    follows = False

    def step(self, text: str) -> None:
        pass

    async def permit(self, what: str, kind: str = "change", detail: str = "") -> bool:
        return True

    async def ask(self, text: str) -> str:
        return "тут"


class _Session:
    id = "s1"
    status = "done"
    text = "Готово."

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def prompt(self, text: str, agent: str = "") -> str:
        self.asked.append(agent)
        return self.text

    async def cancel(self) -> None:
        pass


class _Server:
    """The server's half, as far as `Agent.run` uses it."""

    def __init__(self, session: _Session) -> None:
        self._session = session
        self.model = ""
        self.was_reopened = False

    async def session(self, on_event, *, model: str = "", agent: str = "") -> _Session:
        self.model = model
        return self._session

    async def call(self, *args, **kwargs):
        raise opencode.OpencodeError("no providers here")

    async def reopened(self, session_id, on_event) -> _Session:
        self.was_reopened = True
        raise opencode.OpencodeError("gone")


async def _allow(session, request) -> str:
    return "allow"


def _step(part: dict) -> str:
    """The event text one tool part makes."""
    return opencode._tool_text(part)


def test_a_tool_step_says_what_the_tool_is_doing():
    assert _step({"tool": "bash", "state": {"input": {"command": "ls ~/Downloads | wc -l"}}}) \
        == "bash · ls ~/Downloads | wc -l"
    assert _step({"tool": "read", "state": {"input": {"filePath": "~/Downloads/a.txt"}}}) \
        == "read · ~/Downloads/a.txt"
    assert _step({"tool": "bash", "state": {"input": {}}}) == "bash"


def test_a_long_tool_step_is_cut_to_one_hundred_and_twenty():
    text = _step({"tool": "bash", "state": {"input": {"command": "x" * 200}}})
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


def test_a_file_is_named_however_this_release_spells_it():
    said = agents._what({"toolCall": {
        "kind": "edit", "rawInput": {"filepath": "/Users/x/Documents/resume.pdf"},
    }})
    assert "resume.pdf" in said


def test_a_command_of_assignments_only_is_still_said():
    said = agents._what({"toolCall": {"kind": "execute",
                                      "rawInput": {"command": "A=1 B=2"}}})
    assert "A=1 B=2" in said
    long_said = agents._what({"toolCall": {
        "kind": "execute", "rawInput": {"command": f"A={'.' * 300}"},
    }})
    assert long_said.endswith("…")


def test_a_permission_becomes_the_tool_call_a_person_is_owed():
    request = opencode._as_request({
        "permission": "bash", "patterns": ["ls -la"],
        "metadata": {"command": "ls -la"},
        "tool": {"messageID": "m", "callID": "c"},
    })
    call = request["toolCall"]
    assert call["kind"] == "execute"
    assert call["rawInput"] == {"command": "ls -la"}
    assert call["toolCallId"] == "c"

    request = opencode._as_request({
        "permission": "edit", "patterns": ["note.txt"],
        "metadata": {"filepath": "/tmp/note.txt", "diff": "-a\n+b\n"},
    })
    assert request["toolCall"]["kind"] == "edit"
    assert request["toolCall"]["rawInput"]["filepath"] == "/tmp/note.txt"


async def test_the_model_is_named_when_the_session_is_made(monkeypatch, tmp_path):
    session = _Session()
    server = _Server(session)
    agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None,
                  model="deepseek/other")

    async def started() -> _Server:
        return server

    monkeypatch.setattr(agent, "_started", started)
    await agent.run("зроби щось", _Desk())
    assert server.model == "deepseek/other"


async def test_a_plan_runs_the_turn_as_the_agent_that_only_looks(
    monkeypatch, tmp_path
):
    session = _Session()
    server = _Server(session)
    agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None)

    async def started() -> _Server:
        return server

    monkeypatch.setattr(agent, "_started", started)
    await agent.run("зроби щось", _Desk())
    await agent.run("сплануй", _Desk(), plan=True)
    assert session.asked == ["", "plan"]


async def test_the_agents_own_plan_is_read_for_the_card(tmp_path):
    agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None)
    assert await agent.todo() == []  # no session yet
    await agent.run("зроби щось", _Desk())
    assert [step["content"] for step in await agent.todo()] == ["щось"]
    await agent.close()


async def test_without_a_model_the_default_is_still_named(monkeypatch, tmp_path):
    session = _Session()
    server = _Server(session)
    agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None)

    async def started() -> _Server:
        return server

    monkeypatch.setattr(agent, "_started", started)
    await agent.run("зроби щось", _Desk())
    assert server.model == agents.MODEL


async def test_a_session_that_is_gone_is_made_anew(monkeypatch, tmp_path):
    session = _Session()
    server = _Server(session)
    agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None)

    async def started() -> _Server:
        return server

    monkeypatch.setattr(agent, "_started", started)
    await agent.run("зроби щось", _Desk(), resume="s_old")
    assert server.was_reopened
    assert agent.last_session == session.id


def test_a_model_is_split_the_way_the_server_spells_it():
    assert opencode.split_model("deepseek/deepseek-flash") == {
        "providerID": "deepseek", "id": "deepseek-flash"}
    assert opencode.split_model("no-provider") == {}


async def test_an_env_overlay_keeps_the_opencode_permission_config(
    tmp_path, monkeypatch
):
    seen: dict = {}

    class Captured:
        def __init__(self, name, **kw):
            seen.update(kw)

        async def start(self) -> None:
            pass

        async def close(self) -> None:
            pass

    monkeypatch.setattr(agents.opencode, "Agent", Captured)
    opencode_known = Known("opencode", "opencode",
                           ("opencode", "serve"), "x")
    patch = {"provider": {"deepseek": {"options": {"baseURL": "https://g"}}}}
    agent = Agent(opencode_known, home=tmp_path, env={"CUSTOM": "1"},
                  config_patch=patch, mode="careful")
    await agent._started()

    env = seen["env"]
    assert env["CUSTOM"] == "1"
    config = json.loads(Path(env["OPENCODE_CONFIG"]).read_text(encoding="utf-8"))
    assert config["permission"]["edit"] == "ask"
    assert config["provider"]["deepseek"]["options"]["baseURL"] == "https://g"


def test_installed_is_built_from_the_core_table(monkeypatch):
    monkeypatch.setattr(agents.runner, "detect", lambda: [
        {"type": "opencode", "binary": "opencode", "path": "/x/opencode"},
    ])
    found = installed()
    assert [known.name for known in found] == ["opencode"]
    assert found[0].command == ("opencode", "serve")


async def test_work_that_goes_on_keeps_its_session_and_new_work_starts_clean(tmp_path):
    agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None)
    await agent.run("знайди найбільший файл", _Desk())
    first = agent.last_session

    await agent.run("видали його", _Desk(), resume=first)
    assert agent.last_session == first

    await agent.run("список покупок", _Desk())
    assert agent.last_session != first
    await agent.close()


async def test_a_session_of_another_folder_is_not_continued(tmp_path):
    agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None)
    await agent._started()  # the server exists; the fake says no folder
    server = agent._agent
    assert server is not None
    # A session that names another folder is taken for gone, not for ours.
    assert await agent.run("зроби щось", _Desk(), resume="ses_elsewhere") == "Зроблено."
    assert agent.last_session != "ses_elsewhere"
    await agent.close()


async def test_what_the_task_changed_is_read_from_the_server(tmp_path):
    agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None)
    await agent.run("зроби щось", _Desk())

    found = await agent.changes()
    assert [item["file"] for item in found] == ["note.txt"]
    assert found[0]["additions"] == 1 and found[0]["deletions"] == 1

    await agent.close()
    assert await agent.changes() == []  # a server that is gone tells nothing


async def test_the_hands_say_what_they_did_and_branch_from_it(tmp_path):
    agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None)
    await agent.run("зроби щось", _Desk())
    original = agent.last_session

    assert original in [item["id"] for item in await agent.recent()]
    assert await agent.branch() is True
    assert agent.last_session != original

    await agent.close()
    assert await agent.recent() == []


async def test_undo_puts_the_files_back_and_redo_brings_them_again(tmp_path):
    agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None)
    await agent.run("зроби щось", _Desk())

    assert await agent.undo() == "Повернув як було."
    assert await agent.undo() == "Нічого відкочувати."
    assert await agent.redo() == "Повернув зміну."

    await agent.close()
    assert await agent.undo() == "Нічого відкочувати."


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
        assert permission["question"] == "allow"  # a question to the person already
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
        assert [found[0] for found in seen] == [kind]
        action = seen[0][1]
        assert action["kind"] == "edit"
        # The judge sees the whole action, the diff included.
        assert action["rawInput"]["filepath"].endswith("note.txt")
        assert "diff" in action["rawInput"]
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


async def test_a_question_of_the_agents_own_reaches_the_person(tmp_path):
    agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None)
    asked: list[str] = []

    class Desk(_Desk):
        async def ask(self, text: str) -> str:
            asked.append(text)
            return "у теку"

    result = await agent.run("ASK where", Desk())
    assert asked == ["Куди писати?", "Куди писати? (still)"]
    assert "у теку" in result
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
    agent = opencode.Agent(on_permission=_allow, cwd=work, command=FAKE, env=env)
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


async def test_in_auto_still_the_never_list_is_denied(tmp_path):
    agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None,
                  mode="auto")
    session = _Session()
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

    with pytest.raises(opencode.OpencodeError):
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

    monkeypatch.setattr(opencode.os, "killpg", recording_killpg)
    agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None,
                  tap=lambda direction, message: sent.append(message)
                  if direction == "out" else None)

    class Desk(_Desk):
        stopped = False

        def step(self, text: str) -> None:
            self.stopped = True

    result = await asyncio.wait_for(agent.run("DEAF forever", Desk()), WAIT_S)

    assert result == "Stopped."
    assert any(str(message.get("path")).endswith("/abort") for message in sent)
    assert signal.SIGTERM in killed
    await agent.close()


async def test_a_malformed_frame_does_not_hang_the_turn(tmp_path):
    agent = Agent(KNOWN, cwd=tmp_path, home=tmp_path, command=FAKE, env=None)

    result = await asyncio.wait_for(agent.run("MALFORMED now", _Desk()), WAIT_S)

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
    assert inner._stream is None
    assert await agent.run("зроби ще", _Desk()) == "Зроблено."
    second = agent._agent
    assert second is not None and second._proc is not None
    second_proc = second._proc
    await agent.close()
    assert second_proc.returncode is not None
