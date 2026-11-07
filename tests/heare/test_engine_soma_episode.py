"""A day with the soma: what it holds, what it lets go, what it says.

Run with ``-s`` to see the numbers:

    uv run pytest tests/heare/test_engine_soma_episode.py -s
"""

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


def ended(task_id: int, status: str, request: str) -> Ev:
    return Ev("task", {
        "id": task_id, "request": request, "status": status,
        "news": "готово", "result": "ok",
    })


def tick() -> Ev:
    return Ev("tick", {})


class Clock:
    def __init__(self, moment: float = 1000.0) -> None:
        self.moment = moment

    def __call__(self) -> float:
        return self.moment

    def go(self, seconds: float) -> None:
        self.moment += seconds


def episode(tmp_path, *, soma: Soma | None, daily_limit: int = 99, horizon: float = 30 * 3600):
    """Two tasks start; one is finished, one fails; then a long silence."""
    state = State()
    store = Intents(tmp_path / "intents.json")
    said: list = []
    clock = Clock()
    motor = Engine(store, speak=said.append, now=clock, quiet=("00:00", "00:00"),
                   daily_limit=daily_limit, soma=soma, state=state)

    state.tasks[1] = Task(1, "зроби сайт")
    motor.note(tick())

    clock.go(600)
    state.tasks[2] = Task(2, "онови залежності")
    motor.note(tick())

    clock.go(3600)
    state.tasks[1].status = "done"
    motor.note(ended(1, "done", "зроби сайт"))

    clock.go(3600)
    state.tasks[2].status = "failed"
    motor.note(ended(2, "failed", "онови залежності"))

    clock.go(horizon)
    motor.note(tick())
    motor.note(tick())

    return store, said, motor, clock, state


def soma_intents(store, clock):
    return [i for i in store.alive(clock()) if i.about.startswith("soma:")]


def test_without_a_body_the_episode_says_only_news(tmp_path):
    store, said, _, clock, _ = episode(tmp_path, soma=None)
    assert soma_intents(store, clock) == []
    assert said, "news still has to be told"


def test_with_a_body_the_finished_task_is_let_go(tmp_path):
    store, _, _, clock, _ = episode(tmp_path, soma=Soma())
    assert not [i for i in soma_intents(store, clock) if "зроби сайт" in i.what]


def test_with_a_body_the_failed_task_keeps_pressing(tmp_path):
    store, _, _, clock, _ = episode(tmp_path, soma=Soma(), horizon=1800)
    assert [i for i in soma_intents(store, clock) if "онови залежності" in i.what]


def test_a_failure_fades_instead_of_nagging_for_ever(tmp_path):
    store, _, _, clock, _ = episode(tmp_path, soma=Soma(), horizon=30 * 3600)
    assert not [i for i in soma_intents(store, clock) if "онови залежності" in i.what]


def test_nothing_is_held_twice(tmp_path):
    store, _, _, clock, _ = episode(tmp_path, soma=Soma())
    somas = soma_intents(store, clock)
    assert len(somas) == len({i.about for i in somas})


def test_a_task_is_not_held_the_moment_it_starts(tmp_path):
    state = State({1: Task(1, "зроби сайт")})
    store = Intents(tmp_path / "intents.json")
    clock = Clock()
    motor = Engine(store, speak=lambda m: None, now=clock, quiet=("00:00", "00:00"),
                   daily_limit=99, soma=Soma(), state=state)
    motor.note(tick())
    assert soma_intents(store, clock) == []


def test_the_body_says_less_often_than_it_holds(tmp_path):
    # Two endings are answers to the person and never wait for a budget;
    # what the soma adds on its own is a news intent, and one a day is the cap.
    store, said, _, clock, _ = episode(tmp_path, soma=Soma(), daily_limit=1)
    assert len(said) <= 3


def test_the_episode_report(tmp_path, capsys):
    for soma, label in ((None, "без Соми"), (Soma(), "із Сомою")):
        store, said, motor, clock, _ = episode(tmp_path / str(bool(soma)), soma=soma)
        every = list(store._by_id.values())
        somas = [i for i in every if i.about.startswith("soma:")]
        with capsys.disabled():
            print(
                f"{label:9} усього намірів {len(every):2} · з тіла {len(somas):2} · "
                f"сказано {len(said):2}"
            )
            for intent in somas:
                print(f"            · {intent.what} (вага {intent.weight})")
    assert True
