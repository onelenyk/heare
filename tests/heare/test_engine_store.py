"""The intents' store: on disk, and forgiving of a changed shape."""

from __future__ import annotations

import json

from heare.engine.store import Intents


def test_a_held_intent_survives_a_restart(tmp_path):
    notes = Intents(tmp_path / "intents.json")
    notes.hold("детектор звуку готовий", "task_done", 2)

    again = Intents(tmp_path / "intents.json")
    assert [i.what for i in again.alive(now=100.0)] == ["детектор звуку готовий"]


def test_ids_do_not_repeat(tmp_path):
    notes = Intents(tmp_path / "intents.json")
    notes.hold("перше", "task_done", 2)
    notes.hold("друге", "task_done", 2)
    assert [i.id for i in notes.alive(now=1.0)] == [1, 2]


def test_an_unknown_field_is_dropped(tmp_path):
    path = tmp_path / "intents.json"
    path.write_text(json.dumps([{
        "id": 1, "what": "щось", "why": "task_done", "weight": 2,
        "from_the_future": True,
    }]), encoding="utf-8")

    assert Intents(path).alive(now=1.0)[0].what == "щось"


def test_a_broken_file_is_not_fatal(tmp_path):
    path = tmp_path / "intents.json"
    path.write_text("{ not json", encoding="utf-8")
    assert Intents(path).alive(now=1.0) == []


def test_a_row_missing_something_required_is_skipped(tmp_path):
    path = tmp_path / "intents.json"
    path.write_text(json.dumps([{"id": 1, "what": "без ваги"}]), encoding="utf-8")
    assert Intents(path).alive(now=1.0) == []


def test_a_delivered_intent_leaves_the_list(tmp_path):
    notes = Intents(tmp_path / "intents.json")
    notes.done(notes.hold("щось", "task_done", 2))
    assert notes.alive(now=1.0) == []


def test_an_expired_intent_is_not_in_the_list(tmp_path):
    notes = Intents(tmp_path / "intents.json")
    notes.hold("щось", "task_done", 2, until=50.0)
    assert notes.alive(now=100.0) == []


def test_a_reminder_is_alive_only_between_its_times(tmp_path):
    notes = Intents(tmp_path / "intents.json")
    notes.hold("подзвонити мамі", "reminder", 3,
               not_before=150.0, until=200.0)

    assert notes.alive(now=149.0) == []
    assert [i.what for i in notes.alive(now=150.0)] == ["подзвонити мамі"]
    assert notes.alive(now=201.0) == []


def test_a_reminder_keeps_its_due_time_after_a_restart(tmp_path):
    path = tmp_path / "intents.json"
    Intents(path).hold("подзвонити мамі", "reminder", 3,
                       not_before=150.0, until=200.0)

    again = Intents(path)
    [held] = again.alive(now=150.0)
    assert held.not_before == 150.0
    assert again.alive(now=149.0) == []
