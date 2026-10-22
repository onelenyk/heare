"""The owner's write path: Profile.write and the heare-memory CLI."""

from __future__ import annotations

import io
import os
import stat
import sys

import pytest

from heare.memory.cli import main
from heare.memory.journal import Journal
from heare.memory.profile import Profile


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _run(root, events, *args: str) -> int:
    return main(["--root", str(root), "--events", str(events), *args])


def test_write_renders_and_keeps_the_files_private(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX modes")
    root = tmp_path / "memory"
    path = Profile(root).write("user", "Мене звати Назар.")
    assert path == root / "user.md"
    assert "Мене звати Назар." in Profile(root).render()
    assert _mode(path) == 0o600
    assert _mode(root) == 0o700


def test_append_keeps_the_old_text_and_adds_the_new(tmp_path):
    profile = Profile(tmp_path)
    profile.write("user", "Мене звати Назар.")
    profile.write("user", "Люблю короткі відповіді.", append=True)
    assert (tmp_path / "user.md").read_text(encoding="utf-8") == (
        "Мене звати Назар.\n\nЛюблю короткі відповіді."
    )


def test_an_unknown_block_is_refused(tmp_path):
    with pytest.raises(ValueError, match="not a profile block: nope"):
        Profile(tmp_path).write("nope", "text")


def test_the_cli_sets_shows_adds_and_clears(tmp_path, capsys):
    root, events = tmp_path / "memory", tmp_path / "events"
    assert _run(
        root, events, "set", "user", "--text", "Мене звати Назар."
    ) == 0
    assert _run(root, events, "show") == 0
    assert "Мене звати Назар." in capsys.readouterr().out

    assert _run(
        root, events, "add", "user", "--text", "Люблю короткі відповіді."
    ) == 0
    assert _run(root, events, "show") == 0
    shown = capsys.readouterr().out
    assert "Мене звати Назар." in shown
    assert "Люблю короткі відповіді." in shown

    assert _run(root, events, "clear", "user") == 0
    assert _run(root, events, "show") == 0
    assert "(порожній профіль)" in capsys.readouterr().out


def test_the_cli_reads_the_block_from_stdin(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("Мене звати Назар.\n"))
    root, events = tmp_path / "memory", tmp_path / "events"
    assert _run(root, events, "set", "user") == 0
    assert "Мене звати Назар." in Profile(root).render()


def test_the_cli_refuses_an_unknown_block(tmp_path, capsys):
    assert _run(
        tmp_path / "memory", tmp_path / "events", "set", "nope", "--text", "x"
    ) == 2
    assert "nope" in capsys.readouterr().err


def test_a_write_leaves_a_confirmed_profile_edited_event(tmp_path):
    root, events = tmp_path / "memory", tmp_path / "events"
    _run(root, events, "set", "user", "--text", "Мене звати Назар.")
    edits = [
        row for row in Journal(events).events() if row["kind"] == "profile_edited"
    ]
    assert len(edits) == 1
    assert edits[0]["source"] == "cli"
    assert edits[0]["trust"] == "user_confirmed"
    assert edits[0]["payload"] == {
        "key": "user",
        "chars": len("Мене звати Назар."),
        "append": False,
    }


def test_show_does_not_write_an_event(tmp_path):
    root, events = tmp_path / "memory", tmp_path / "events"
    _run(root, events, "set", "user", "--text", "Мене звати Назар.")
    before = len(list(Journal(events).events()))
    _run(root, events, "show")
    assert len(list(Journal(events).events())) == before
