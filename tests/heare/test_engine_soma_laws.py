"""The soma under the laws: it may ask, never insist, and never speak itself."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from heare.engine import Engine, Intents
from heare.engine.soma import Soma


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


def engine(tmp_path, *, now: float = 1000.0, soma=None, state=None,
           quiet=("00:00", "00:00"), daily_limit=99):
    store = Intents(tmp_path / "intents.json")
    said: list = []
    clock = Clock(now)
    motor = Engine(
        store, speak=said.append, now=clock, quiet=quiet,
        daily_limit=daily_limit, soma=soma, state=state,
    )
    return store, said, motor, clock


def ripened(tmp_path, **over):
    soma = over.pop("soma", Soma())
    store, said, motor, clock = engine(tmp_path, soma=soma, **over)
    motor.note(event())
    clock.go(3600)
    motor.note(tick())
    return store, said, motor, clock, soma


def soma_intents(store, clock):
    return [i for i in store.alive(clock()) if i.about.startswith("soma:")]


def test_the_soma_never_speaks_itself():
    soma = Soma()
    soma.feed("task_failed", thing_id="task:7", kind="task", label="Сайт готовий.")
    assert soma.spoke("task:7") is None
    assert not hasattr(soma, "_speak")
    assert not hasattr(soma, "speaker")


def test_holding_and_ticking_says_nothing_on_its_own(tmp_path):
    store, said, motor, clock = engine(tmp_path, soma=Soma())
    motor.note(event())
    before = len(said)
    clock.go(3600)
    motor._advance()
    assert len(said) == before
    assert soma_intents(store, clock) != []


def test_the_person_mid_turn_is_not_interrupted(tmp_path):
    store, _, motor, clock, _ = ripened(tmp_path, state=State(phase="listening"))
    motor.note(tick())
    clock.go(3600)
    motor.note(tick())
    assert soma_intents(store, clock) != []


def test_quiet_hours_hold_a_ripened_need(tmp_path):
    night = datetime(2026, 10, 1, 23, 0).timestamp()
    store, _, motor, clock, _ = ripened(
        tmp_path, now=night, quiet=("22:00", "08:00")
    )
    motor.note(tick())
    assert soma_intents(store, clock) != []


def test_the_daily_budget_limits_the_soma(tmp_path):
    store, said, motor, clock = engine(tmp_path, soma=Soma(), daily_limit=1)
    motor.note(event())
    clock.go(3600)
    motor.note(tick())
    assert any(i.why == "soma" and i.status == "delivered" for i in store._by_id.values())

    motor.note(Ev("task", {"id": 8, "request": "інша", "news": "Інша", "status": "failed"}))
    clock.go(3600)
    motor.note(tick())
    motor.note(tick())
    assert [i for i in store.alive(clock()) if i.about == "soma:8"], "over budget, held"


def test_a_spoken_want_does_not_re_press(tmp_path):
    soma = Soma()
    soma.feed("task_failed", thing_id="task:7", kind="task", label="Сайт готовий.")
    assert soma.propose() is not None
    soma.spoke("task:7")
    assert soma.propose() is None  # she said her piece; time alone won't re-raise
    soma.tick(3 * 3600)
    assert soma.propose() is None  # habituation holds


def test_the_soma_does_not_name_the_person():
    soma = Soma()
    soma.feed("task_failed", thing_id="task:7", kind="task", label="Сайт готовий.")
    soma.tick(3600)
    text = soma.state().lower()
    assert "owner" not in text
    assert "task:7" not in text


def test_the_soma_cannot_reach_the_store_or_the_speaker():
    soma = Soma()
    assert not hasattr(soma, "_intents")
    assert not hasattr(soma, "store")
    assert not hasattr(soma, "_speak")
