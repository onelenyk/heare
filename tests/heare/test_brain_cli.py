"""The brain's command line: `unpair` takes a machine's key back."""

from __future__ import annotations

import json
import sys

from heare import brain
from heare.api import auth
from heare.config import Settings


def _store(tmp_path, *pids: str):
    path = tmp_path / "part_tokens.json"
    path.write_text(json.dumps({pid: auth.fingerprint(f"key-{pid}") for pid in pids}))
    return path


def _run(monkeypatch, tmp_path, tokens, *args: str) -> int:
    settings = Settings(path=tmp_path / "config.toml", part_tokens_file=tokens)
    monkeypatch.setattr(brain, "Settings", lambda: settings)
    monkeypatch.setattr(brain.layout, "settle", lambda *a, **k: None)
    monkeypatch.setattr(brain.logs, "setup", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["heare-brain", *args])
    return brain.main()


def _stored(path) -> dict[str, str]:
    return json.loads(path.read_text())


def test_unpair_revokes_only_the_named_machine(tmp_path, monkeypatch, capsys):
    tokens = _store(tmp_path, "machine@kitchen", "oled@esp32c3-07a1",
                    "window@phone")
    before = _stored(tokens)

    assert _run(monkeypatch, tmp_path, tokens, "unpair", "kitchen") == 0

    after = _stored(tokens)
    assert set(after) == {"oled@esp32c3-07a1", "window@phone"}
    assert after["oled@esp32c3-07a1"] == before["oled@esp32c3-07a1"]
    assert after["window@phone"] == before["window@phone"]
    assert capsys.readouterr().out == "revoked machine@kitchen\n"


def test_unpair_missing_machine_exits_one_and_keeps_every_key(
        tmp_path, monkeypatch, capsys):
    tokens = _store(tmp_path, "machine@kitchen", "oled@esp32c3-07a1")
    before = _stored(tokens)

    assert _run(monkeypatch, tmp_path, tokens, "unpair", "nope") == 1

    assert _stored(tokens) == before
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no such machine: nope" in captured.err


def test_unpair_pid_revokes_the_machine_and_that_part(
        tmp_path, monkeypatch, capsys):
    tokens = _store(tmp_path, "machine@kitchen", "oled@esp32c3-07a1",
                    "window@phone")

    assert _run(monkeypatch, tmp_path, tokens, "unpair", "kitchen",
                "--pid", "oled@esp32c3-07a1") == 0

    assert set(_stored(tokens)) == {"window@phone"}
    out = capsys.readouterr().out
    assert "revoked machine@kitchen" in out
    assert "revoked oled@esp32c3-07a1" in out


def test_unpair_pid_alone_revokes_just_that_part(tmp_path, monkeypatch, capsys):
    tokens = _store(tmp_path, "machine@kitchen", "oled@esp32c3-07a1")

    assert _run(monkeypatch, tmp_path, tokens, "unpair",
                "--pid", "oled@esp32c3-07a1") == 0

    assert set(_stored(tokens)) == {"machine@kitchen"}
    assert capsys.readouterr().out == "revoked oled@esp32c3-07a1\n"


def test_unpair_missing_pid_exits_one_but_still_revokes_the_machine(
        tmp_path, monkeypatch, capsys):
    tokens = _store(tmp_path, "machine@kitchen", "oled@esp32c3-07a1")

    assert _run(monkeypatch, tmp_path, tokens, "unpair", "kitchen",
                "--pid", "ghost@esp32") == 1

    assert set(_stored(tokens)) == {"oled@esp32c3-07a1"}
    captured = capsys.readouterr()
    assert captured.out == "revoked machine@kitchen\n"
    assert "no such part: ghost@esp32" in captured.err


def test_unpair_without_a_name_or_pid_changes_nothing(
        tmp_path, monkeypatch, capsys):
    tokens = _store(tmp_path, "machine@kitchen")
    before = _stored(tokens)

    assert _run(monkeypatch, tmp_path, tokens, "unpair") == 1

    assert _stored(tokens) == before
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unpair:" in captured.err
