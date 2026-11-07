"""A restart is a waking up, not a new soma."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from heare.engine import Soma, Engine, Intents


@dataclass
class Ev:
    kind: str
    data: dict


def event(status: str = "failed") -> Ev:
    return Ev("task", {"id": 7, "request": "зроби сайт", "news": "Сайт готовий.", "status": status})


def tick() -> Ev:
    return Ev("tick", {})


class Clock:
    def __init__(self, moment: float = 1000.0) -> None:
        self.moment = moment

    def __call__(self) -> float:
        return self.moment

    def go(self, seconds: float) -> None:
        self.moment += seconds


def test_the_body_survives_a_round_trip(tmp_path):
    soma = Soma()
    soma.feed("task_failed", "task:7", "Сайт готовий.")
    soma.tick(3600)
    path = tmp_path / "soma.json"
    soma.save(path)

    again = Soma.load(path)
    assert again.t == soma.t
    assert again.state() == soma.state()
    assert len(again.needs) == len(soma.needs)
    assert len(again.intentions) == len(soma.intentions)


def test_a_missing_file_gives_a_new_body(tmp_path):
    soma = Soma.load(tmp_path / "nothing.json")
    assert soma.t == 0.0
    assert len(soma.needs) == 1


def test_a_broken_file_gives_a_new_body(tmp_path):
    path = tmp_path / "soma.json"
    path.write_text("{ not json", encoding="utf-8")
    assert Soma.load(path).t == 0.0


def test_a_file_of_another_version_gives_a_new_body(tmp_path):
    path = tmp_path / "soma.json"
    path.write_text(json.dumps({"version": 999, "t": 500.0}), encoding="utf-8")
    assert Soma.load(path).t == 0.0


def test_unknown_fields_are_dropped_and_missing_take_defaults(tmp_path):
    path = tmp_path / "soma.json"
    path.write_text(json.dumps({
        "version": 1,
        "t": 120.0,
        "something_new": "ignore me",
        "needs": [{"id": 1, "drive": "closure", "about": "x", "label": "x", "level": 0.5, "extra": 1}],
    }), encoding="utf-8")
    soma = Soma.load(path)
    assert soma.t == 120.0
    assert soma.needs[0].level == 0.5
    assert soma.needs[0].urgency == 0.0


def test_the_engine_saves_the_body_as_it_turns(tmp_path):
    path = tmp_path / "soma.json"
    clock = Clock()
    motor = Engine(Intents(tmp_path / "intents.json"), speak=lambda m: None,
                   now=clock, quiet=("00:00", "00:00"), daily_limit=99,
                   soma=Soma(), soma_path=path)
    motor.note(event())
    clock.go(3600)
    motor.note(tick())
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_a_restart_keeps_the_pressure(tmp_path):
    path = tmp_path / "soma.json"
    store = Intents(tmp_path / "intents.json")
    clock = Clock()
    first = Engine(store, speak=lambda m: None, now=clock, quiet=("00:00", "00:00"),
                   daily_limit=99, soma=Soma(), soma_path=path)
    first.note(event())
    clock.go(3600)
    first.note(tick())
    before = Soma.load(path).state()

    second = Engine(store, speak=lambda m: None, now=clock, quiet=("00:00", "00:00"),
                    daily_limit=99, soma_path=path)
    assert second._soma is not None
    assert second._soma.state() == before


def test_a_restart_does_not_hold_the_same_need_twice(tmp_path):
    path = tmp_path / "soma.json"
    store = Intents(tmp_path / "intents.json")
    clock = Clock()
    first = Engine(store, speak=lambda m: None, now=clock, quiet=("00:00", "00:00"),
                   daily_limit=0, soma=Soma(), soma_path=path)
    first.note(event())
    clock.go(3600)
    first.note(tick())
    held = [i for i in store.alive(clock()) if i.about.startswith("soma:")]
    assert len(held) == 1

    second = Engine(store, speak=lambda m: None, now=clock, quiet=("00:00", "00:00"),
                    daily_limit=0, soma_path=path)
    clock.go(60)
    second.note(tick())
    assert len([i for i in store.alive(clock()) if i.about.startswith("soma:")]) == 1


def test_pressure_is_saved_even_before_anything_ripens(tmp_path):
    path = tmp_path / "soma.json"
    soma = Soma()
    soma.feed("new_fact", "x", "x")
    clock = Clock()
    motor = Engine(Intents(tmp_path / "intents.json"), speak=lambda m: None,
                   now=clock, quiet=("00:00", "00:00"), daily_limit=99,
                   soma=soma, soma_path=path)
    assert soma.propose() is None
    clock.go(600)
    motor.note(tick())
    assert path.exists()
    assert Soma.load(path).t > 0.0


def test_without_a_path_nothing_is_written(tmp_path):
    clock = Clock()
    motor = Engine(Intents(tmp_path / "intents.json"), speak=lambda m: None,
                   now=clock, quiet=("00:00", "00:00"), daily_limit=99, soma=Soma())
    motor.note(event())
    clock.go(3600)
    motor.note(tick())
    assert list(Path(tmp_path).glob("soma*")) == []
