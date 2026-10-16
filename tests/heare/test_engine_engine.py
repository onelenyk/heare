"""The engine's first job: work ending becomes an intent, and is told."""

from __future__ import annotations

from dataclasses import dataclass

from heare.engine import Engine, Intents


@dataclass
class Ev:
    kind: str
    data: dict


def event(status, **over):
    data = {"id": 7, "request": "зроби сайт", "news": "Сайт готовий.", "status": status}
    data.update(over)
    return Ev("task", data)


def engine(tmp_path):
    store = Intents(tmp_path / "intents.json")
    said: list = []
    motor = Engine(store, speak=said.append, now=lambda: 100.0)
    return store, said, motor


def test_a_finished_task_is_kept_and_told(tmp_path):
    store, said, motor = engine(tmp_path)
    motor.note(event("done"))
    assert len(said) == 1
    assert "Сайт готовий." in said[0].text
    assert said[0].person is False
    assert store.alive(now=100.0) == []  # told, so it leaves the held list


def test_a_failed_task_is_told_too(tmp_path):
    _store, said, motor = engine(tmp_path)
    motor.note(event("failed", news="Не вдалося зібрати."))
    assert len(said) == 1 and "Не вдалося" in said[0].text


def test_a_stopped_task_is_not_told(tmp_path):
    store, said, motor = engine(tmp_path)
    motor.note(event("stopped"))
    assert said == [] and store.alive(now=100.0) == []


def test_a_running_task_is_not_told(tmp_path):
    _store, said, motor = engine(tmp_path)
    motor.note(event("running"))
    assert said == []


def test_a_task_with_nothing_to_say_is_not_told(tmp_path):
    store, said, motor = engine(tmp_path)
    motor.note(event("done", news=""))
    assert said == [] and store.alive(now=100.0) == []


def test_an_unrelated_event_is_ignored(tmp_path):
    _store, said, motor = engine(tmp_path)
    motor.note(Ev("phase", {"phase": "idle"}))
    assert said == []


def test_with_no_way_to_speak_the_intent_waits(tmp_path):
    store = Intents(tmp_path / "intents.json")
    Engine(store, now=lambda: 100.0).note(event("done"))
    assert [i.why for i in store.alive(now=100.0)] == ["task_done"]
