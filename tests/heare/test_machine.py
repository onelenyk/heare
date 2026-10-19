"""What this machine runs: a part is a folder, so turning it on or off
is writing or removing one — and a key is checked before it is trusted."""

from __future__ import annotations

import json
import sys

import httpx
import pytest

from heare import keeper, machine


@pytest.fixture
def bin_dir(tmp_path):
    folder = tmp_path / "bin"
    folder.mkdir()
    for name in machine.PARTS:
        (folder / f"heare-{name}").write_text("#!/bin/sh\n", encoding="utf-8")
    return folder


def test_turning_a_part_on_writes_what_the_keeper_reads(tmp_path, bin_dir):
    parts = tmp_path / "parts"
    machine.turn(parts, "voice", True, bin_dir)

    assert keeper.wanted(parts) == {"voice": [str(bin_dir / "heare-voice")]}
    manifest = json.loads((parts / "voice" / "heare.json").read_text(encoding="utf-8"))
    assert manifest["channel"] == "voice" and manifest["forms"] == ["input", "output"]
    assert not (parts / "voice" / "heare.json.new").exists()


def test_turning_a_part_off_takes_its_folder(tmp_path, bin_dir):
    parts = tmp_path / "parts"
    machine.turn(parts, "hands", True, bin_dir)
    machine.turn(parts, "hands", False, bin_dir)

    assert keeper.wanted(parts) == {}
    assert machine.running(parts) == set()


def test_a_part_in_the_app_is_a_module_of_its_python(tmp_path):
    # No scripts beside the keeper: the part is found as heare.<name>,
    # and the keeper runs it with its own interpreter.
    found = machine.command("keeper", tmp_path)
    assert found == ["python", "-m", "heare.keeper"]
    assert keeper._resolve(found)[0] == sys.executable


def test_a_part_that_is_not_here_cannot_be_turned_on(tmp_path, monkeypatch):
    monkeypatch.setattr(machine, "command", lambda name, bin_dir=None: None)
    monkeypatch.setattr(machine.sys, "platform", "darwin")

    with pytest.raises(ValueError, match="не встановлена"):
        machine.turn(tmp_path / "parts", "voice", True, tmp_path)
    assert machine.running(tmp_path / "parts") == set()


def test_eyes_are_a_mac_s_only(tmp_path, bin_dir, monkeypatch):
    monkeypatch.setattr(machine.sys, "platform", "linux")
    listed = {p["name"]: p for p in machine.parts(tmp_path / "parts", bin_dir)}

    assert listed["eyes"]["why_not"] == "лише на Mac"
    assert listed["voice"]["why_not"] is None and listed["voice"]["on"] is False


@pytest.mark.parametrize(("status", "ok", "said"), [
    (200, True, "живий"), (401, False, "ключ не підходить"),
    (500, False, "провайдер відповів 500")])
def test_a_key_is_asked_of_its_provider(monkeypatch, status, ok, said):
    seen = {}

    def get(url, headers, timeout):
        seen.update(url=url, auth=headers["Authorization"])
        return httpx.Response(status)

    monkeypatch.setattr(machine.httpx, "get", get)
    assert machine.check_key("GROQ_API_KEY", " gsk_x ") == (ok, said)
    assert seen == {"url": "https://api.groq.com/openai/v1/models", "auth": "Bearer gsk_x"}


def test_a_key_with_no_way_to_reach_its_provider_says_so(monkeypatch):
    def get(url, headers, timeout):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(machine.httpx, "get", get)
    assert machine.check_key("DEEPSEEK_API_KEY", "sk")[0] is False
