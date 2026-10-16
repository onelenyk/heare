"""The engine's first job: work ending becomes an intent, and is told."""

from __future__ import annotations

from dataclasses import dataclass, field

from heare.engine import Engine, Intents


@dataclass
class Ev:
    kind: str
    data: dict


def event(status, **over):
    data = {"id": 7, "request": "зроби сайт", "news": "Сайт готовий.", "status": status}
    data.update(over)
    return Ev("task", data)


def engine(tmp_path):
    store = Intents(tmp_path / "intents.json")
    said: list = []
    motor = Engine(store, speak=said.append, now=lambda: 100.0)
    return store, said, motor


def test_a_finished_task_is_kept_and_told(tmp_path):
    store, said, motor = engine(tmp_path)
    motor.note(event("done"))
    assert len(said) == 1
    assert "Сайт готовий." in said[0].text
    assert said[0].person is False
    assert store.alive(now=100.0) == []  # told, so it leaves the held list


def test_a_failed_task_is_told_too(tmp_path):
    _store, said, motor = engine(tmp_path)
    motor.note(event("failed", news="Не вдалося зібрати."))
    assert len(said) == 1 and "Не вдалося" in said[0].text


def test_a_stopped_task_is_not_told(tmp_path):
    store, said, motor = engine(tmp_path)
    motor.note(event("stopped"))
    assert said == [] and store.alive(now=100.0) == []


def test_a_running_task_is_not_told(tmp_path):
    _store, said, motor = engine(tmp_path)
    motor.note(event("running"))
    assert said == []


def test_a_task_with_nothing_to_say_is_not_told(tmp_path):
    store, said, motor = engine(tmp_path)
    motor.note(event("done", news=""))
    assert said == [] and store.alive(now=100.0) == []


def test_an_unrelated_event_is_ignored(tmp_path):
    _store, said, motor = engine(tmp_path)
    motor.note(Ev("phase", {"phase": "idle"}))
    assert said == []


def test_with_no_way_to_speak_the_intent_waits(tmp_path):
    store = Intents(tmp_path / "intents.json")
    Engine(store, now=lambda: 100.0).note(event("done"))
    assert [i.why for i in store.alive(now=100.0)] == ["task_done"]


@dataclass
class State:
    phase: str = "idle"
    around: dict = field(default_factory=dict)


def test_it_waits_while_the_person_is_away(tmp_path):
    store = Intents(tmp_path / "intents.json")
    said: list = []
    state = State(around={"present": False})
    motor = Engine(store, state=state, speak=said.append, now=lambda: 100.0)

    motor.note(event("done"))
    assert said == []  # away: it waits
    assert [i.what for i in store.alive(now=100.0)] == ["Сайт готовий."]

    state.around = {"present": True}
    motor.note(Ev("around", {}))
    assert len(said) == 1 and "Сайт готовий." in said[0].text


def test_it_waits_while_the_brain_is_mid_turn(tmp_path):
    store = Intents(tmp_path / "intents.json")
    said: list = []
    state = State(phase="speaking")
    motor = Engine(store, state=state, speak=said.append, now=lambda: 100.0)

    motor.note(event("done"))
    assert said == []  # mid-reply: it waits
    state.phase = "idle"
    motor.note(Ev("phase", {}))
    assert len(said) == 1


def test_several_finished_tasks_are_told_in_one_breath(tmp_path):
    store = Intents(tmp_path / "intents.json")
    said: list = []
    state = State(around={"present": False})
    motor = Engine(store, state=state, speak=said.append, now=lambda: 100.0)

    motor.note(event("done", id=1, news="Перше готове."))
    motor.note(event("done", id=2, news="Друге готове."))
    assert said == []  # away: both kept

    state.around = {"present": True}
    motor.note(Ev("around", {}))
    assert len(said) == 1  # one remark, not two
    assert "Перше готове." in said[0].text and "Друге готове." in said[0].text


def test_an_answered_remark_makes_the_next_one_sooner(tmp_path):
    store = Intents(tmp_path / "intents.json")
    said: list = []
    clock = {"t": 0.0}
    motor = Engine(store, speak=said.append, now=lambda: clock["t"])

    motor.note(event("done", id=1, news="Перше"))  # told at t=0
    motor.note(event("done", id=2, news="Друге"))
    assert len(said) == 1  # too soon: held

    clock["t"] = 10.0
    motor.note(Ev("heard", {"text": "дякую"}))  # answered, so it grows bolder
    clock["t"] = 200.0
    motor.note(Ev("phase", {}))
    assert len(said) == 2  # sooner than the base gap of four minutes
