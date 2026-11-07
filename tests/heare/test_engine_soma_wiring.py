"""The engine with a soma: nothing is held until the need has grown."""

from __future__ import annotations

from dataclasses import dataclass

from heare.engine import Engine, Intents
from heare.engine.soma import Soma


@dataclass
class Ev:
    kind: str
    data: dict


def event(status: str, **over: object) -> Ev:
    data = {"id": 7, "request": "зроби сайт", "news": "Сайт готовий.", "status": status}
    data.update(over)
    return Ev("task", data)


def tick() -> Ev:
    return Ev("tick", {})


class Clock:
    def __init__(self, moment: float = 1000.0) -> None:
        self.moment = moment

    def __call__(self) -> float:
        return self.moment

    def go(self, seconds: float) -> None:
        self.moment += seconds


def engine(tmp_path, *, soma: Soma | None = None):
    store = Intents(tmp_path / "intents.json")
    said: list = []
    clock = Clock()
    motor = Engine(
        store, speak=said.append, now=clock,
        quiet=("00:00", "00:00"), daily_limit=99, soma=soma,
    )
    return store, said, motor, clock


def test_without_a_body_the_result_is_said_at_once(tmp_path):
    _, said, motor, _ = engine(tmp_path)
    motor.note(event("done"))
    assert len(said) == 1


def test_with_a_body_the_news_is_still_said_at_once(tmp_path):
    _, said, motor, _ = engine(tmp_path, soma=Soma())
    motor.note(event("done"))
    assert len(said) == 1


def test_a_failure_becomes_an_intent_of_its_own_once_it_has_grown(tmp_path):
    store, _, motor, clock = engine(tmp_path, soma=Soma())
    motor.note(event("failed"))
    clock.go(3600)
    motor.note(tick())
    soma = [i for i in store.alive(clock()) if i.about.startswith("soma:")]
    assert len(soma) == 1


def test_a_finished_task_leaves_nothing_pressing(tmp_path):
    store, _, motor, clock = engine(tmp_path, soma=Soma())
    motor.note(event("done"))
    clock.go(3600)
    motor.note(tick())
    assert [i for i in store.alive(clock()) if i.about.startswith("soma:")] == []


def test_time_is_what_grows_the_need_not_events(tmp_path):
    store, _, motor, clock = engine(tmp_path, soma=Soma())
    motor.note(event("failed"))
    motor.note(tick())
    motor.note(tick())
    assert [i for i in store.alive(clock()) if i.about.startswith("soma:")] == []
    clock.go(3600)
    motor.note(tick())
    assert len([i for i in store.alive(clock()) if i.about.startswith("soma:")]) == 1


def test_the_same_need_is_not_held_twice(tmp_path):
    store, _, motor, clock = engine(tmp_path, soma=Soma())
    motor.note(event("failed"))
    clock.go(3600)
    motor.note(tick())
    clock.go(600)
    motor.note(tick())
    clock.go(600)
    motor.note(tick())
    assert len([i for i in store.alive(clock()) if i.about.startswith("soma:")]) == 1


def test_speaking_does_not_make_an_intent(tmp_path):
    store, _, motor, clock = engine(tmp_path, soma=Soma())
    motor.note(Ev("heard", {}))
    clock.go(3600)
    motor.note(tick())
    assert store.alive(clock()) == []


def test_a_failed_task_keeps_on_pressing_after_the_news_is_stale(tmp_path):
    store, _, motor, clock = engine(tmp_path, soma=Soma())
    motor.note(event("failed"))
    clock.go(3 * 3600)
    motor.note(tick())
    alive = store.alive(clock())
    assert [i for i in alive if i.why == "task_failed"] == []
    assert [i for i in alive if i.about.startswith("soma:")] != []


def test_the_intent_keeps_a_deadline(tmp_path):
    store, _, motor, clock = engine(tmp_path, soma=Soma())
    motor.note(event("failed"))
    clock.go(3600)
    motor.note(tick())
    soma = [i for i in store.alive(clock()) if i.about.startswith("soma:")]
    assert soma[0].until is not None


def test_the_weight_stays_inside_what_the_form_allows(tmp_path):
    store, _, motor, clock = engine(tmp_path, soma=Soma())
    motor.note(event("failed"))
    clock.go(3600)
    motor.note(tick())
    soma = [i for i in store.alive(clock()) if i.about.startswith("soma:")]
    assert 1 <= soma[0].weight <= 3


def test_the_news_is_said_the_same_with_a_body_and_without(tmp_path):
    _, said_body, motor_body, _ = engine(tmp_path, soma=Soma())
    motor_body.note(event("done"))
    _, said_plain, motor_plain, _ = engine(tmp_path)
    motor_plain.note(event("done"))
    assert len(said_body) == len(said_plain) == 1
    assert said_body[0].text == said_plain[0].text
