"""What the keeper tells the brain, and what it does with the answer."""

from __future__ import annotations

import httpx
import pytest

from heare import machine, report
from heare.config import Settings
from heare.core.i18n import _


def test_a_report_names_every_part_and_its_channel(tmp_path):
    folder = tmp_path / "parts"
    folder.mkdir()
    machine.turn(folder, "voice", True)

    written = report.describe(folder)
    voice = next(one for one in written["parts"] if one["name"] == "voice")
    assert voice["channel"] == "voice" and voice["on"] is True
    assert written["platform"] and written["version"]
    assert written["mem_total"] is None or written["mem_total"] > 0
    assert written["mem_free"] is None or written["mem_free"] >= 0


def test_the_person_s_wish_turns_the_manifest(tmp_path):
    folder = tmp_path / "parts"
    folder.mkdir()
    machine.turn(folder, "voice", True)

    assert report.apply(folder, {"voice": False}) == ["voice"]
    assert "voice" not in machine.running(folder)
    assert report.apply(folder, {"voice": True}) == ["voice"]
    assert "voice" in machine.running(folder)


def test_a_name_this_machine_does_not_know_is_ignored(tmp_path):
    folder = tmp_path / "parts"
    folder.mkdir()
    assert report.apply(folder, {"teleporter": True, "voice": "yes"}) == []
    assert not machine.running(folder)


def test_the_report_backs_off_when_the_brain_is_away():
    assert report.wait_after(0) == report.EVERY_S
    assert report.wait_after(1) == 2 * report.EVERY_S
    assert report.wait_after(20) == report.BACKOFF_MAX_S


@pytest.mark.parametrize("status,halted", [(401, True), (403, True), (503, False)])
async def test_a_report_refused_for_its_key_says_the_machine_is_forgotten(
        tmp_path, monkeypatch, status, halted):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="no token")

    class Refusing(httpx.AsyncClient):
        def __init__(self, **kw):
            kw.setdefault("transport", httpx.MockTransport(handler))
            super().__init__(**kw)

    monkeypatch.setattr(report.httpx, "AsyncClient", Refusing)
    token = tmp_path / "api_token"
    token.write_text("master", encoding="utf-8")
    settings = Settings(path=tmp_path / "config.toml", api_token_file=token,
                        machine_token_file=tmp_path / "machine")
    folder = tmp_path / "parts"
    folder.mkdir()
    told: list[str] = []

    once = report.Report(settings, folder, on_refused=told.append)
    assert await once.once() is False
    if halted:
        assert told == [_("the brain no longer knows this machine — pair it again")]
    else:
        assert told == [], "a brain that is merely unwell is waited out"
