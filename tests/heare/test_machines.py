"""The machines the brain keeps: what a keeper says, and what was asked."""

from __future__ import annotations

from heare.api.machines import Machines


def report(**over: object) -> dict:
    data = {
        "name": "mac",
        "version": "0.9.10",
        "platform": "darwin",
        "arch": "arm64",
        "load": [0.4, 0.5, 0.6],
        "disk_free": 32_000_000_000,
        "disk_total": 500_000_000_000,
        "parts": [{
            "name": "voice", "label": "Voice", "what": "listens and speaks",
            "channel": "voice", "on": True, "state": "running", "why": "",
        }],
    }
    data.update(over)
    return data


def test_a_report_is_kept_and_read_back(tmp_path):
    machines = Machines(tmp_path / "machines.json")
    assert machines.report("machine@mac", report(), now=100.0) == {}

    view = machines.view({}, now=110.0)
    assert [machine["name"] for machine in view] == ["mac"]
    machine = view[0]
    assert machine["state"] == "online"
    assert machine["version"] == "0.9.10"
    assert machine["load"] == [0.4, 0.5, 0.6]
    assert machine["parts"][0]["label"] == "Voice"
    assert machine["parts"][0]["on"] is True


def test_a_machine_that_stopped_talking_is_away(tmp_path):
    machines = Machines(tmp_path / "machines.json")
    machines.report("machine@mac", report(), now=100.0)
    assert machines.view({}, now=100.0)[0]["state"] == "online"
    assert machines.view({}, now=100.0 + 600)[0]["state"] == "away"


def test_what_the_person_asked_for_comes_back(tmp_path):
    machines = Machines(tmp_path / "machines.json")
    machines.report("machine@mac", report(), now=100.0)
    assert machines.desire("machine@mac", "voice", False)
    assert machines.report("machine@mac", report(), now=200.0) == {"voice": False}


def test_the_wish_and_the_truth_are_both_visible(tmp_path):
    machines = Machines(tmp_path / "machines.json")
    machines.report("machine@mac", report(), now=100.0)
    machines.desire("machine@mac", "voice", False)

    part = machines.view({}, now=110.0)[0]["parts"][0]
    assert part["on"] is True, "the keeper has not turned it off yet"
    assert part["desired"] is False, "the person's wish is kept apart"


def test_a_report_survives_a_restart(tmp_path):
    path = tmp_path / "machines.json"
    machines = Machines(path)
    machines.report("machine@mac", report(), now=100.0)
    machines.desire("machine@mac", "eyes", True)

    again = Machines(path)
    assert again.wanted("machine@mac") == {"eyes": True}
    assert again.view({}, now=110.0)[0]["version"] == "0.9.10"


def test_a_broken_file_is_a_brain_that_heard_nobody(tmp_path):
    path = tmp_path / "machines.json"
    path.write_text("{ not json", encoding="utf-8")
    assert Machines(path).view({}, now=0.0) == []


def test_an_unknown_field_is_dropped(tmp_path):
    path = tmp_path / "machines.json"
    path.write_text('[{"pid": "machine@mac", "from_the_future": true, '
                    '"parts": [{"name": "voice", "evil": "x"}]}]', encoding="utf-8")
    part = Machines(path).view({}, now=0.0)[0]["parts"][0]
    assert part["name"] == "voice" and "evil" not in part


def test_a_part_name_is_not_a_path(tmp_path):
    machines = Machines(tmp_path / "machines.json")
    assert not machines.desire("machine@mac", "../voice", True)
    assert not machines.desire("machine@mac", "voice/x", True)


def test_a_part_on_the_wire_appears_without_a_report(tmp_path):
    known = {"voice@mac": {
        "channel": "voice", "forms": ["input", "output"], "about": "microphone",
        "host": "mac", "state": "approved", "version": "0.9.10",
        "machine": None, "name": "Voice",
    }}
    machine = Machines(tmp_path / "machines.json").view(known, now=1.0)[0]
    assert machine["reported"] is False
    assert machine["state"] == "online"
    assert machine["parts"][0]["online"] is True
    assert machine["parts"][0]["pids"] == ["voice@mac"]
