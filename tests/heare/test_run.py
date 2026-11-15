"""The terminal: one command on this machine, the mode's leave, a list never run."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from heare import native_hands
from heare.native_hands import NativeHand, refused


class Desk:
    """What the work asks the brain for, as the tests need it."""

    follows, asked = False, ""

    def __init__(self, allow: bool = True, stop_at: int = 0) -> None:
        self.allow, self.stop_at = allow, stop_at
        self.permits: list[str] = []
        self.steps = 0
        self.stopped = False

    def step(self, text: str) -> None:
        self.steps += 1
        if self.stop_at and self.steps >= self.stop_at:
            self.stopped = True

    async def permit(self, what: str, kind: str = "change", detail: str = "") -> bool:
        self.permits.append(kind)
        return self.allow

    async def ask(self, text: str) -> str:
        return ""


def hand(mode: str = "auto") -> NativeHand:
    return NativeHand(None, None, SimpleNamespace(executor_mode=mode))  # type: ignore[arg-type]


async def test_a_command_runs_and_says_its_exit():
    said = await hand()._run({"command": "echo hi"}, Desk())
    assert "$ echo hi" in said and "hi" in said and "exit 0" in said


async def test_a_failing_command_keeps_its_code():
    said = await hand()._run({"command": "false"}, Desk())
    assert said.endswith("exit 1") and "hi" not in said


async def test_the_folder_is_where_it_runs(tmp_path):
    said = await hand()._run({"command": "pwd", "cwd": str(tmp_path)}, Desk())
    assert str(tmp_path.resolve()) in said


async def test_a_folder_that_is_not_there_is_said_so():
    said = await hand()._run({"command": "pwd", "cwd": "/no/such/folder/here"}, Desk())
    assert said.startswith("Failed: no such folder")


async def test_a_command_that_hangs_is_stopped():
    started = asyncio.get_running_loop().time()
    said = await hand()._run({"command": "sleep 5", "timeout": 1}, Desk())
    assert said.startswith("Failed: no end in 1 s")
    assert asyncio.get_running_loop().time() - started < 3


async def test_too_much_output_is_cut():
    said = await hand()._run(
        {"command": f"{sys.executable} -c \"print(chr(120)*40000)\""}, Desk())
    assert "… cut" in said and len(said) < native_hands.OUTPUT_CHARS + 200


@pytest.mark.parametrize("command", [
    "sudo ls", "sudoedit /etc/hosts", "doas rm x", "security dump-keychain",
    "launchctl list", "csrutil disable", "spctl --master-disable",
    "diskutil eraseDisk JHFS+ x disk2", "rm -rf /", "rm -fr / --no-preserve-root",
    "cat ~/.heare/machine/api_token", "cat $HOME/.ssh/id_rsa",
])
def test_never_run(command):
    assert refused(command)


@pytest.mark.parametrize("command", [
    "ls -la", "brew install htop", "open -a Safari", "defaults read com.apple.dock",
    "osascript -e 'tell app \"Finder\" to activate'", "rm -rf ./build",
])
def test_everything_else_is_allowed(command):
    assert refused(command) == ""


async def test_a_refused_command_does_not_run(tmp_path):
    mark = tmp_path / "mark"
    said = await hand()._run({"command": f"touch {mark}; sudo ls"}, Desk())
    assert said.startswith("Not done: sudo") and not mark.exists()


async def test_the_mode_says_who_agrees():
    quiet = Desk()
    await hand("auto")._run({"command": "true"}, quiet)
    assert quiet.permits == []

    judged = Desk()
    await hand("normal")._run({"command": "true"}, judged)
    assert judged.permits == ["judge"]

    person = Desk()
    await hand("careful")._run({"command": "true"}, person)
    assert person.permits == ["change"]


async def test_a_refused_leave_decides_nothing_runs(tmp_path):
    mark = tmp_path / "mark"
    said = await hand("careful")._run({"command": f"touch {mark}"}, Desk(allow=False))
    assert said.startswith("Not done") and not mark.exists()


async def test_the_shell_carries_no_keys(monkeypatch):
    monkeypatch.setenv("HEARE_TEST_KEY", "sk-secret")
    said = await hand()._run({"command": "env"}, Desk())
    assert "HEARE_TEST_KEY" not in said
    assert "PATH=/opt/homebrew/bin" in said


async def test_stop_kills_the_command(monkeypatch):
    monkeypatch.setattr(native_hands, "WATCH_S", 0.05)
    desk = Desk(stop_at=2)  # the first step is the command itself, the second the ping
    started = asyncio.get_running_loop().time()
    with pytest.raises(asyncio.CancelledError):
        await hand()._run({"command": "sleep 5"}, desk)
    assert asyncio.get_running_loop().time() - started < 3


async def test_a_stopped_job_starts_nothing(tmp_path):
    mark = tmp_path / "mark"
    desk = Desk()
    desk.stopped = True
    with pytest.raises(asyncio.CancelledError):
        await hand()._run({"command": f"touch {mark}"}, desk)
    assert not mark.exists()
