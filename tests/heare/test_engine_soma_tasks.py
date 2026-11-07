"""Work that is still running is what the body keeps."""

from __future__ import annotations

from dataclasses import dataclass, field

from heare.engine import Body, Engine, Intents


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


def engine(tmp_path, *, state=None, body=None):
    store = Intents(tmp_path / "intents.json")
    said: list = []
    clock = Clock()
    motor = Engine(store, speak=said.append, now=clock, quiet=("00:00", "00:00"),
                   daily_limit=0, body=body if body is not None else Body(), state=state)
    return store, said, motor, clock


def test_a_running_task_becomes_a_thing_left_open(tmp_path):
    state = State({1: Task(1, "зроби сайт")})
    store, _, motor, clock = engine(tmp_path, state=state)
    motor.note(Ev("tick", {}))
    body = motor._body
    assert [n for n in body.needs if n.drive == "closure" and n.about == "task:1"]


def test_the_same_running_task_is_not_opened_twice(tmp_path):
    state = State({1: Task(1, "зроби сайт")})
    _, _, motor, clock = engine(tmp_path, state=state)
    motor.note(Ev("tick", {}))
    before = next(n for n in motor._body.needs if n.about == "task:1").level
    clock.go(600)
    motor.note(Ev("tick", {}))
    motor.note(Ev("tick", {}))
    after = next(n for n in motor._body.needs if n.about == "task:1").level
    assert after < before + 0.05


def test_a_running_task_turns_into_an_intent_when_it_is_left_alone(tmp_path):
    state = State({1: Task(1, "зроби сайт")})
    store, _, motor, clock = engine(tmp_path, state=state)
    motor.note(Ev("tick", {}))
    clock.go(3600)
    motor.note(Ev("tick", {}))
    soma = [i for i in store.alive(clock()) if i.about.startswith("soma:")]
    assert soma
    assert soma[0].what == "зроби сайт"


def test_finishing_the_task_closes_it(tmp_path):
    state = State({1: Task(1, "зроби сайт")})
    store, _, motor, clock = engine(tmp_path, state=state)
    motor.note(Ev("tick", {}))
    clock.go(3600)
    state.tasks[1].status = "done"
    motor.note(ended(1, "done"))
    clock.go(7200)
    motor.note(Ev("tick", {}))
    assert not [i for i in store.alive(clock()) if i.what == "зроби сайт"]


def test_stopping_the_task_closes_it_too(tmp_path):
    state = State({1: Task(1, "зроби сайт")})
    store, _, motor, clock = engine(tmp_path, state=state)
    motor.note(Ev("tick", {}))
    clock.go(3600)
    state.tasks[1].status = "stopped"
    motor.note(ended(1, "stopped"))
    clock.go(7200)
    motor.note(Ev("tick", {}))
    assert not [i for i in store.alive(clock()) if i.what == "зроби сайт"]


def test_a_failed_task_keeps_pressing(tmp_path):
    state = State({1: Task(1, "зроби сайт")})
    store, _, motor, clock = engine(tmp_path, state=state)
    motor.note(Ev("tick", {}))
    clock.go(3600)
    state.tasks[1].status = "failed"
    motor.note(ended(1, "failed"))
    clock.go(3600)
    motor.note(Ev("tick", {}))
    assert [i for i in store.alive(clock()) if i.about.startswith("soma:")]


def test_nothing_is_watched_without_a_body(tmp_path):
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
    assert motor._body is not None
    assert not [n for n in motor._body.needs if n.drive == "closure"]
