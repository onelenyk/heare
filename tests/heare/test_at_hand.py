"""The native hands on their own machine: looking is free, changing is asked."""

from __future__ import annotations

import pytest

from heare import at_hand


class Desk:
    def __init__(self, yes: bool) -> None:
        self.yes, self.asked, self.stopped = yes, [], False

    def step(self, what: str) -> None:
        pass

    async def permit(self, what, kind, detail="") -> bool:
        self.asked.append(kind)
        return self.yes


@pytest.mark.parametrize("line", [
    "ls -la", "cat notes.txt", "git status", "find . -name x", "grep foo a.txt"])
def test_looking_is_free(line):
    assert at_hand.is_free(line.split())


@pytest.mark.parametrize("line", [
    "rm -rf x", "git push", "git commit", "find . -delete", "find . -exec rm {} ;",
    "grep -r foo .", "tail -f log", "cat ~/.ssh/id_rsa", "cat .env",
    "cat ~/.heare/machine/keys.json", "security dump-keychain", "curl x"])
def test_whatever_else_is_not(line):
    assert not at_hand.is_free(line.split())


async def test_a_refused_command_does_not_run(tmp_path):
    mark = tmp_path / "mark"
    desk = Desk(False)
    said = await at_hand.run(at_hand.RUN, {"command": f"touch {mark}"}, desk)
    assert said.startswith("Not done") and not mark.exists()
    assert desk.asked == ["change"]


async def test_a_command_runs_without_a_shell(tmp_path):
    mark = tmp_path / "mark"
    await at_hand.run(at_hand.RUN, {"command": f"ls ; touch {mark}"}, Desk(True))
    assert not mark.exists()


async def test_writing_asks_the_person_and_never_the_judge(tmp_path):
    path = tmp_path / "a.txt"
    desk = Desk(True)
    await at_hand.run(at_hand.WRITE, {"path": str(path), "text": "hi"}, desk)
    assert path.read_text() == "hi" and desk.asked == ["change"]


async def test_a_secret_is_not_read_by_the_free_path():
    said = await at_hand.run(at_hand.READ, {"path": "~/.ssh/id_rsa"}, Desk(True))
    assert said.startswith("Not done")


async def test_deep_work_has_pipes_inside_the_fence_and_stays_there(monkeypatch, tmp_path):
    monkeypatch.setattr(at_hand.Path, "home", lambda: tmp_path.resolve())
    desk = Desk(False)
    said = await at_hand.run(at_hand.RUN, {"command": "echo hi | tr h H"}, desk, deep=True)
    assert "Hi" in said and desk.asked == []
    outside = tmp_path / "outside"
    await at_hand.run(at_hand.RUN, {"command": f"touch {outside}"}, desk, deep=True)
    assert not outside.exists()


async def test_an_edit_needs_the_piece_there_once(tmp_path, monkeypatch):
    monkeypatch.setattr(at_hand.Path, "home", lambda: tmp_path.resolve())
    path = tmp_path / "heare-work" / "a.txt"
    path.parent.mkdir()
    path.write_text("a b a")
    args = {"path": str(path), "old": "a", "new": "c"}
    assert (await at_hand.run(at_hand.EDIT, args, Desk(True))).startswith("Failed")
    await at_hand.run(at_hand.EDIT, {**args, "old": "b"}, Desk(False))
    assert path.read_text() == "a c a"


async def test_the_network_is_the_persons_word(tmp_path, monkeypatch):
    monkeypatch.setattr(at_hand.Path, "home", lambda: tmp_path.resolve())
    said = await at_hand.run(at_hand.RUN, {"command": "true", "network": "yes"},
                             Desk(False), deep=True)
    assert said.startswith("Not done")


async def test_a_plan_outlives_the_try(tmp_path, monkeypatch):
    monkeypatch.setattr(at_hand.Path, "home", lambda: tmp_path.resolve())
    await at_hand.run(at_hand.PLAN, {"text": "- [x] a\n- [ ] b"}, Desk(True), task="t")
    assert "- [ ] b" in at_hand.plan_of("t").read_text()
