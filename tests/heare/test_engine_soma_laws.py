"""The body under the laws: it may ask, never insist, and never speak itself."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from heare.engine import Engine, Intents
from heare.engine.soma import Body


@dataclass
class Ev:
    kind: str
    data: dict


def event(status: str = "failed") -> Ev:
    return Ev("task", {"id": 7, "request": "зроби сайт", "news": "Сайт готовий.", "status": status})


def tick() -> Ev:
    return Ev("tick", {})


class Clock:
    def __init__(self, moment: float) -> None:
        self.moment = moment

    def __call__(self) -> float:
        return self.moment

    def go(self, seconds: float) -> None:
        self.moment += seconds


@dataclass
class State:
    phase: str = "idle"
    present: bool | None = None

    def around_any(self, name: str):
        return self.present


def engine(tmp_path, *, now: float = 1000.0, body=None, state=None,
           quiet=("00:00", "00:00"), daily_limit=99):
    store = Intents(tmp_path / "intents.json")
    said: list = []
    clock = Clock(now)
    motor = Engine(
        store, speak=said.append, now=clock, quiet=quiet,
        daily_limit=daily_limit, body=body, state=state,
    )
    return store, said, motor, clock


def ripened(tmp_path, **over):
    """A body with one failure that has already grown enough to be held."""
    body = over.pop("body", Body())
    store, said, motor, clock = engine(tmp_path, body=body, **over)
    motor.note(event())
    clock.go(3600)
    motor.note(tick())
    return store, said, motor, clock, body


def test_the_body_never_speaks_itself():
    body = Body()
    body.feed("task_failed", "task:7", "Сайт готовий.")
    body.tick(3600)
    assert body.propose() is not None
    assert not hasattr(body, "speak")


def test_holding_and_ticking_says_nothing_on_its_own(tmp_path):
    store, said, motor, clock = engine(tmp_path, body=Body())
    motor.note(event())
    before = len(said)
    clock.go(3600)
    motor._advance()
    assert len(said) == before
    assert [i for i in store.alive(clock()) if i.about.startswith("soma:")] != []


def test_the_person_mid_turn_is_not_interrupted(tmp_path):
    store, _, motor, clock, _ = ripened(tmp_path, state=State(phase="listening"))
    motor.note(tick())
    clock.go(3600)
    motor.note(tick())
    assert [i for i in store.alive(clock()) if i.about.startswith("soma:")] != []


def test_quiet_hours_hold_a_ripened_need(tmp_path):
    night = datetime(2026, 10, 1, 23, 0).timestamp()
    store, _, motor, clock, _ = ripened(
        tmp_path, now=night, quiet=("22:00", "08:00")
    )
    motor.note(tick())
    assert [i for i in store.alive(clock()) if i.about.startswith("soma:")] != []


def test_the_daily_budget_holds_a_ripened_need(tmp_path):
    store, said, motor, clock = engine(tmp_path, body=Body(), daily_limit=1)
    motor.note(event("done"))
    motor.note(event())
    clock.go(3600)
    motor.note(tick())
    motor.note(tick())
    soma = [i for i in store.alive(clock()) if i.about.startswith("soma:")]
    assert soma, "the ripened need must still be waiting, not said over the budget"


def test_a_need_fades_instead_of_nagging_forever(tmp_path):
    store, _, motor, clock = engine(tmp_path, body=Body(), daily_limit=0)
    motor.note(event())
    clock.go(3600)
    motor.note(tick())
    label = "soma:зроби сайт"
    assert [i for i in store.alive(clock()) if i.about == label]
    clock.go(30 * 3600)
    motor.note(tick())
    assert not [i for i in store.alive(clock()) if i.about == label]


def test_the_body_does_not_name_the_person(tmp_path):
    _, _, motor, clock, body = ripened(tmp_path)
    text = body.state()
    assert "owner" not in text.lower()
    self_needs = [n for n in body.needs if n.about == "self"]
    assert self_needs
    assert all(n.about == "self" for n in self_needs)


def test_the_body_cannot_reach_the_store_or_the_speaker():
    body = Body()
    assert not hasattr(body, "_intents")
    assert not hasattr(body, "store")
    assert not hasattr(body, "speak")
