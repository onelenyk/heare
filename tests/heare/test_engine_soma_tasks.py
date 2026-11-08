"""Work that is still running is what the soma keeps."""

from __future__ import annotations

from dataclasses import dataclass, field

from heare.engine import Soma, Engine, Intents


@dataclass
class Ev:
    kind: str
    data: dict


@dataclass
class Task:
    id: int
    request: str
    status: str = "running"
    executor: str = "opencode"


@dataclass
class State:
    tasks: dict = field(default_factory=dict)
    phase: str = "idle"

    def around_any(self, name: str):
        return None


def ended(task_id: int, status: str, request: str = "зроби сайт") -> Ev:
    return Ev("task", {
        "id": task_id, "request": request, "status": status,
        "news": "Сайт готовий.", "result": "ok",
    })


class Clock:
    def __init__(self, moment: float = 1000.0) -> None:
        self.moment = moment

    def __call__(self) -> float:
        return self.moment

    def go(self, seconds: float) -> None:
        self.moment += seconds


def engine(tmp_path, *, state=None, soma=None):
    store = Intents(tmp_path / "intents.json")
    said: list = []
    clock = Clock()
    motor = Engine(store, speak=said.append, now=clock, quiet=("00:00", "00:00"),
                   daily_limit=0, soma=soma if soma is not None else Soma(), state=state)
    return store, said, motor, clock


def soma_intents(store, clock):
    return [i for i in store.alive(clock()) if i.about.startswith("soma:")]


def test_a_running_task_becomes_a_thing_left_open(tmp_path):
    state = State({1: Task(1, "зроби сайт")})
    store, _, motor, clock = engine(tmp_path, state=state)
    motor.note(Ev("tick", {}))
    soma = motor._soma
    assert [w for w in soma.wants if w.verb == "finish" and w.thing_id == "1"]


def test_the_same_running_task_is_not_opened_twice(tmp_path):
    state = State({1: Task(1, "зроби сайт")})
    _, _, motor, clock = engine(tmp_path, state=state)
    motor.note(Ev("tick", {}))
    before = next(w for w in motor._soma.wants if w.thing_id == "1").care
    clock.go(600)
    motor.note(Ev("tick", {}))
    motor.note(Ev("tick", {}))
    after = next(w for w in motor._soma.wants if w.thing_id == "1").care
    assert after < before + 0.05


def test_a_running_task_turns_into_an_intent_when_it_is_left_alone(tmp_path):
    state = State({1: Task(1, "зроби сайт")})
    store, _, motor, clock = engine(tmp_path, state=state)
    motor.note(Ev("tick", {}))
    clock.go(3 * 3600)
    motor.note(Ev("tick", {}))
    soma = soma_intents(store, clock)
    assert soma
    assert soma[0].what == "зроби сайт"


def test_finishing_the_task_closes_it(tmp_path):
    state = State({1: Task(1, "зроби сайт")})
    store, _, motor, clock = engine(tmp_path, state=state)
    motor.note(Ev("tick", {}))
    clock.go(3 * 3600)
    state.tasks[1].status = "done"
    motor.note(ended(1, "done"))
    clock.go(7200)
    motor.note(Ev("tick", {}))
    assert not [i for i in soma_intents(store, clock) if i.what == "зроби сайт"]


def test_stopping_the_task_closes_it_too(tmp_path):
    state = State({1: Task(1, "зроби сайт")})
    store, _, motor, clock = engine(tmp_path, state=state)
    motor.note(Ev("tick", {}))
    clock.go(3 * 3600)
    state.tasks[1].status = "stopped"
    motor.note(ended(1, "stopped"))
    clock.go(7200)
    motor.note(Ev("tick", {}))
    assert not [i for i in soma_intents(store, clock) if i.what == "зроби сайт"]


def test_a_failed_task_keeps_pressing(tmp_path):
    state = State({1: Task(1, "зроби сайт")})
    store, _, motor, clock = engine(tmp_path, state=state)
    motor.note(Ev("tick", {}))
    clock.go(3600)
    state.tasks[1].status = "failed"
    motor.note(ended(1, "failed"))
    clock.go(3600)
    motor.note(Ev("tick", {}))
    assert soma_intents(store, clock)


def test_nothing_is_watched_without_a_soma(tmp_path):
    state = State({1: Task(1, "зроби сайт")})
    store = Intents(tmp_path / "intents.json")
    clock = Clock()
    motor = Engine(store, speak=lambda m: None, now=clock, quiet=("00:00", "00:00"),
                   daily_limit=0, state=state)
    motor.note(Ev("tick", {}))
    clock.go(3600)
    motor.note(Ev("tick", {}))
    assert motor.mood() == ""


def test_no_state_no_watching(tmp_path):
    store, _, motor, clock = engine(tmp_path, state=None)
    motor.note(Ev("tick", {}))
    assert motor._soma is not None
    assert not [w for w in motor._soma.wants if w.verb == "finish"]
