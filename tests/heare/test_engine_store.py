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


def test_reminders_lists_only_held_reminders_in_due_order(tmp_path):
    notes = Intents(tmp_path / "intents.json")
    later = notes.hold("пізніше", "reminder", 3,
                       not_before=300.0, until=400.0)
    sooner = notes.hold("раніше", "reminder", 3,
                        not_before=100.0, until=400.0)
    untimed = notes.hold("без часу", "reminder", 3)
    delivered = notes.hold("доставлене", "reminder", 3,
                           not_before=100.0, until=400.0)
    notes.done(delivered)
    notes.hold("старе", "reminder", 3, not_before=50.0, until=150.0)
    notes.hold("справа", "task_done", 2, not_before=100.0)

    assert [i.id for i in notes.reminders(now=200.0)] == [
        untimed.id, sooner.id, later.id,
    ]


def test_cancel_drops_a_held_reminder_for_good(tmp_path):
    path = tmp_path / "intents.json"
    notes = Intents(path)
    held = notes.hold("подзвонити мамі", "reminder", 3, not_before=150.0)

    assert notes.cancel(held.id) is True
    assert notes.reminders(now=150.0) == []
    assert notes.alive(now=150.0) == []

    again = Intents(path)
    assert again.reminders(now=150.0) == []
    assert again.alive(now=150.0) == []


def test_cancel_refuses_unknown_non_reminder_and_done(tmp_path):
    notes = Intents(tmp_path / "intents.json")
    work = notes.hold("зроби", "task_done", 2)
    reminder = notes.hold("подзвонити", "reminder", 3, not_before=150.0)
    delivered = notes.hold("доставлене", "reminder", 3, not_before=150.0)
    notes.done(delivered)

    assert notes.cancel(999) is False
    assert notes.cancel(work.id) is False
    assert notes.cancel(work.id, why="reminder") is False
    assert notes.cancel(delivered.id) is False
    assert notes.cancel(reminder.id) is True
