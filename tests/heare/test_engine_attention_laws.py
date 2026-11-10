"""The attention under the laws: it may ask, never insist, and never speak itself."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from heare.engine import Engine, Intents
from heare.engine.attention import Attention


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


def engine(tmp_path, *, now: float = 1000.0, attention=None, state=None,
           quiet=("00:00", "00:00"), daily_limit=99):
    store = Intents(tmp_path / "intents.json")
    said: list = []
    clock = Clock(now)
    motor = Engine(
        store, speak=said.append, now=clock, quiet=quiet,
        daily_limit=daily_limit, attention=attention, state=state,
    )
    return store, said, motor, clock


def ripened(tmp_path, **over):
    attention = over.pop("attention", Attention())
    store, said, motor, clock = engine(tmp_path, attention=attention, **over)
    motor.note(event())
    clock.go(3600)
    motor.note(tick())
    return store, said, motor, clock, attention


def attention_intents(store, clock):
    return [i for i in store.alive(clock()) if i.about.startswith("soma:")]


def test_the_attention_never_speaks_itself():
    attention = Attention()
    attention.feed("task_failed", thing_id="task:7", kind="task", label="Сайт готовий.")
    assert attention.spoke("task:7") is None
    assert not hasattr(attention, "_speak")
    assert not hasattr(attention, "speaker")


def test_holding_and_ticking_says_nothing_on_its_own(tmp_path):
    store, said, motor, clock = engine(tmp_path, attention=Attention())
    motor.note(event())
    before = len(said)
    clock.go(3600)
    motor._advance()
    assert len(said) == before
    assert attention_intents(store, clock) != []


def test_the_person_mid_turn_is_not_interrupted(tmp_path):
    store, _, motor, clock, _ = ripened(tmp_path, state=State(phase="listening"))
    motor.note(tick())
    clock.go(3600)
    motor.note(tick())
    assert attention_intents(store, clock) != []


def test_quiet_hours_hold_a_ripened_need(tmp_path):
    night = datetime(2026, 10, 1, 23, 0).timestamp()
    store, _, motor, clock, _ = ripened(
        tmp_path, now=night, quiet=("22:00", "08:00")
    )
    motor.note(tick())
    assert attention_intents(store, clock) != []


def test_the_daily_budget_limits_the_attention(tmp_path):
    store, said, motor, clock = engine(tmp_path, attention=Attention(), daily_limit=1)
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
    attention = Attention()
    attention.feed("task_failed", thing_id="task:7", kind="task", label="Сайт готовий.")
    assert attention.hottest().pressing >= 0.5
    attention.spoke("task:7")
    assert attention.hottest().pressing < 0.5  # she said her piece; time alone won't re-raise
    attention.tick(3 * 3600)
    assert attention.hottest().pressing < 0.5  # habituation holds


def test_tiredness_raises_the_bar(tmp_path):
    _, _, motor, _, attention = ripened(tmp_path)
    rested = motor._bar()
    attention.energy = 0.2
    assert motor._bar() > rested


def test_the_attention_does_not_name_the_person():
    attention = Attention()
    attention.feed("task_failed", thing_id="task:7", kind="task", label="Сайт готовий.")
    attention.tick(3600)
    text = attention.state().lower()
    assert "owner" not in text
    assert "task:7" not in text


def test_the_attention_cannot_reach_the_store_or_the_speaker():
    attention = Attention()
    assert not hasattr(attention, "_intents")
    assert not hasattr(attention, "store")
    assert not hasattr(attention, "_speak")
