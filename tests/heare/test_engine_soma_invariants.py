"""The three states agree: what presses, the things it binds, and the notes.

The soul keeps wants on disk, the store keeps intents on disk, and the engine
holds one from the other. Three writers, two files — so the agreement between
them is said here once, and checked after every step and across a restart.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from heare.engine import Engine, Intents, Soma
from heare.engine.intent import SOMA, tagged
from heare.engine.soma import HELD_AT_MOST
from heare.language import uk


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
class State:
    phase: str = "idle"
    tasks: dict = field(default_factory=dict)

    def around_any(self, name):
        return None


class Clock:
    def __init__(self, moment: float = 1_700_000_000.0) -> None:
        self.moment = moment

    def __call__(self) -> float:
        return self.moment

    def go(self, seconds: float) -> None:
        self.moment += seconds


def problems(soma: Soma, intents: Intents, now: float) -> list[str]:
    """Every way the three could disagree, named one by one."""
    found: list[str] = []
    if len(soma.wants) > HELD_AT_MOST:
        found.append(f"{len(soma.wants)} wants, more than {HELD_AT_MOST}")
    seen: set[tuple[str, str]] = set()
    for want in soma.wants:
        thing = soma.thing(want.thing_id)
        if thing is None:
            found.append(f"a want on a thing that is gone: {want.verb} {want.thing_id}")
            continue
        if thing.state != "alive":
            found.append(f"a want on a {thing.state} thing: {want.verb} {want.thing_id}")
        if (want.verb, want.thing_id) in seen:
            found.append(f"two wants {want.verb} on {want.thing_id}")
        seen.add((want.verb, want.thing_id))
        if not 0.0 <= want.care <= 1.0 or not 0.0 <= want.pressing <= 1.0:
            found.append(f"a want out of range: {want.verb} {want.thing_id}")
        if want.said < 0:
            found.append(f"said below zero: {want.verb} {want.thing_id}")
    if soma.last_spoken is not None and soma.thing(soma.last_spoken) is None:
        found.append(f"last_spoken on a thing that is gone: {soma.last_spoken}")

    abouts: list[str] = []
    for intent in intents.alive(now):
        reference = tagged(intent.about, SOMA)
        if reference is None:
            continue
        abouts.append(intent.about)
        thing = soma.thing(reference)
        if thing is None or thing.state != "alive":
            found.append(f"a held remark for a thing that is gone: {intent.about}")
    if len(abouts) != len(set(abouts)):
        found.append(f"two held remarks for one thing: {abouts}")
    return found


def simulate(tmp_path, *, days: int = 3, limit: int = 3, seed: int = 11):
    clock = Clock()
    store = Intents(tmp_path / "intents.json")
    soma = Soma()
    state = State()
    motor = Engine(
        store, speak=lambda message: None, now=clock, quiet=("00:00", "00:00"),
        daily_limit=limit, soma=soma, state=state, open_loop=uk.open_loop,
    )
    rng = random.Random(seed)
    end = clock() + days * 86400
    next_event = clock()
    tid = 0
    while clock() < end:
        if clock() >= next_event:
            tid += 1
            roll = rng.random()
            if roll < 0.45:
                state.tasks[tid] = Task(tid, f"task {tid}")
                motor.note(Ev("task", {
                    "id": tid, "request": f"task {tid}", "status": "running",
                }))
            elif roll < 0.72:
                state.tasks.pop(tid, None)
                motor.note(Ev("task", {
                    "id": tid, "request": f"task {tid}",
                    "news": f"task {tid} done", "status": "done",
                }))
            elif roll < 0.88:
                state.tasks.pop(tid, None)
                motor.note(Ev("task", {
                    "id": tid, "request": f"task {tid}",
                    "news": f"task {tid} failed", "status": "failed",
                }))
            else:
                motor.note(Ev("heard", {
                    "person": True, "channel": "voice", "text": "треба подзвонити мамі",
                }))
            next_event += rng.randint(600, 5400)
            found = problems(soma, store, clock())
            assert not found, found
        clock.go(60)
        motor.note(Ev("tick"))
        found = problems(soma, store, clock())
        assert not found, found
    return soma, store, clock, motor


def test_three_days_keep_the_agreement(tmp_path):
    simulate(tmp_path)


def test_a_week_with_no_budget_keeps_the_agreement(tmp_path):
    simulate(tmp_path, days=7, limit=99)


def test_a_restart_keeps_the_agreement(tmp_path):
    soma, store, clock, _ = simulate(tmp_path, days=2)
    path = tmp_path / "soma.json"
    soma.save(path)

    again = Soma.load(path)
    found = problems(again, Intents(tmp_path / "intents.json"), clock())
    assert not found, found


def test_a_released_want_leaves_no_remark(tmp_path):
    clock = Clock()
    store = Intents(tmp_path / "intents.json")
    soma = Soma()
    state = State()
    motor = Engine(
        store, speak=lambda message: None, now=clock, quiet=("00:00", "00:00"),
        daily_limit=0, soma=soma, state=state,
    )
    for n in range(HELD_AT_MOST + 6):
        state.tasks[n] = Task(n, f"task {n}")
        motor.note(Ev("task", {"id": n, "request": f"task {n}", "status": "running"}))
        clock.go(3600)
        motor.note(Ev("tick"))
        found = problems(soma, store, clock())
        assert not found, found
    assert [i for i in store.alive(clock()) if i.why == SOMA], "a held remark is still there"
