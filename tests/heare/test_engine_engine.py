"""The engine's first job: a piece of work ending becomes an intent."""

from __future__ import annotations

from dataclasses import dataclass

from heare.engine import Engine, Intents


@dataclass
class Ev:
    kind: str
    data: dict


def event(status, **over):
    data = {"id": 7, "request": "зроби сайт", "result": "Готово.", "status": status}
    data.update(over)
    return Ev("task", data)


def engine(tmp_path):
    store = Intents(tmp_path / "intents.json")
    return store, Engine(store, now=lambda: 100.0)


def test_a_finished_task_becomes_an_intent(tmp_path):
    store, motor = engine(tmp_path)
    motor.note(event("done"))
    alive = store.alive(now=100.0)
    assert [i.why for i in alive] == ["task_done"]
    assert alive[0].what == "Готово."
    assert alive[0].about == "task:7"
    assert alive[0].until == 100.0 + 2 * 3600


def test_a_failed_task_is_news_too(tmp_path):
    store, motor = engine(tmp_path)
    motor.note(event("failed", result="Не вдалося"))
    assert [i.why for i in store.alive(now=100.0)] == ["task_failed"]


def test_a_stopped_task_is_not_news(tmp_path):
    store, motor = engine(tmp_path)
    motor.note(event("stopped"))
    assert store.alive(now=100.0) == []


def test_a_running_task_is_not_news(tmp_path):
    store, motor = engine(tmp_path)
    motor.note(event("running"))
    assert store.alive(now=100.0) == []


def test_an_unrelated_event_is_ignored(tmp_path):
    store, motor = engine(tmp_path)
    motor.note(Ev("phase", {"phase": "idle"}))
    assert store.alive(now=100.0) == []


def test_a_task_with_no_result_falls_back_to_the_request(tmp_path):
    store, motor = engine(tmp_path)
    motor.note(event("done", result=""))
    assert store.alive(now=100.0)[0].what == "зроби сайт"
