"""The bench for the soma: seven simulated days of a working person.

This is the base measurement that every later change is compared against.
It simulates days, not real time: the clock is a number, advanced a minute
at a time, and the events come from a fixed seed so the numbers hold.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from heare.engine import Soma, Engine, Intents


@dataclass
class Ev:
    kind: str
    data: dict = field(default_factory=dict)


@dataclass
class Task:
    id: int
    request: str
    status: str = "running"
    executor: str = "opencode"


@dataclass
class FakeState:
    phase: str = "idle"
    tasks: dict = field(default_factory=dict)

    def around_any(self, name: str):
        return None


class Clock:
    def __init__(self, moment: float = 1_700_000_000.0) -> None:
        self.moment = moment

    def __call__(self) -> float:
        return self.moment

    def go(self, seconds: float) -> None:
        self.moment += seconds


def simulate(tmp_path, *, days: int = 7, daily_limit: int, seed: int = 7) -> dict:
    clock = Clock()
    said: list = []
    store = Intents(tmp_path / "intents.json")
    soma = Soma()
    state = FakeState()
    engine = Engine(store, speak=said.append, now=clock, quiet=("00:00", "00:00"),
                    daily_limit=daily_limit, soma=soma, state=state)

    rng = random.Random(seed)
    end = clock() + days * 86400
    next_event = clock()
    tid = 0
    min_energy = 1.0
    low_energy_ticks = 0
    max_held = 0
    while clock() < end:
        if clock() >= next_event:
            tid += 1
            roll = rng.random()
            if roll < 0.45:
                state.tasks[tid] = Task(tid, f"task {tid}")
                engine.note(Ev("task", {
                    "id": tid, "request": f"task {tid}", "status": "running",
                }))
            elif roll < 0.72:
                state.tasks.pop(tid, None)
                engine.note(Ev("task", {
                    "id": tid, "request": f"task {tid}",
                    "news": f"task {tid} done", "status": "done",
                }))
            elif roll < 0.88:
                state.tasks.pop(tid, None)
                engine.note(Ev("task", {
                    "id": tid, "request": f"task {tid}",
                    "news": f"task {tid} failed", "status": "failed",
                }))
            else:
                engine.note(Ev("heard"))
            next_event += rng.randint(600, 5400)
        clock.go(60)
        engine.note(Ev("tick"))
        min_energy = min(min_energy, soma.energy)
        if soma.energy < 0.15:
            low_energy_ticks += 1
        max_held = max(max_held, len(
            [i for i in store.alive(clock()) if i.about.startswith("soma:")]))

    delivered = [i for i in store._by_id.values() if i.status == "delivered"]
    return {
        "soma_delivered": len([i for i in delivered if i.why == "soma"]),
        "all_delivered": len(delivered),
        "still_held_soma": len(
            [i for i in store.alive(clock()) if i.about.startswith("soma:")]),
        "max_held_soma": max_held,
        "wants_end": len(soma.wants),
        "things_end": len(soma.things),
        "trace_end": len(soma.trace),
        "energy_end": round(soma.energy, 3),
        "min_energy": round(min_energy, 3),
        "hours_below_0_15": round(low_energy_ticks / 60, 1),
    }


def test_a_working_week_on_a_budget_of_one(tmp_path):
    from heare.engine.soma import HELD_AT_MOST

    measured = simulate(tmp_path, daily_limit=1)
    print("daily_limit=1", measured)
    assert 3 <= measured["soma_delivered"] <= 15
    assert measured["min_energy"] >= 0.30
    assert measured["still_held_soma"] <= HELD_AT_MOST
    assert measured["wants_end"] <= HELD_AT_MOST, "attention carried more than a dozen things"


def test_a_working_week_on_a_budget_of_three(tmp_path):
    from heare.engine.soma import HELD_AT_MOST

    measured = simulate(tmp_path, daily_limit=3)
    print("daily_limit=3", measured)
    assert measured["soma_delivered"] > 15
    assert measured["min_energy"] >= 0.05
    assert measured["wants_end"] <= HELD_AT_MOST


def test_a_working_week_with_no_budget(tmp_path):
    from heare.engine.soma import HELD_AT_MOST

    measured = simulate(tmp_path, daily_limit=99)
    print("daily_limit=99", measured)
    # The list of wants is capped now, so fewer distinct things can be
    # raised than before (352 on 9 November, 107 with the cap): still far
    # past any budget, and the energy still dies.
    assert measured["soma_delivered"] > 80
    assert measured["min_energy"] < 0.10
    assert measured["hours_below_0_15"] > 24
    assert measured["wants_end"] <= HELD_AT_MOST
