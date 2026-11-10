"""What the keeper tells the brain, and what it does with the answer."""

from __future__ import annotations

from heare import machine, report


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
