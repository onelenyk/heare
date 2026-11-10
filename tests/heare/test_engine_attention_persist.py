"""A restart is a waking up, not a new attention."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from heare.engine import Attention, Engine, Intents


@dataclass
class Ev:
    kind: str
    data: dict


def event(status: str = "failed") -> Ev:
    return Ev("task", {"id": 7, "request": "зроби сайт", "news": "Сайт готовий.", "status": status})


def tick() -> Ev:
    return Ev("tick", {})


class Clock:
    def __init__(self, moment: float = 1000.0) -> None:
        self.moment = moment

    def __call__(self) -> float:
        return self.moment

    def go(self, seconds: float) -> None:
        self.moment += seconds


def test_the_attention_survives_a_round_trip(tmp_path):
    attention = Attention()
    attention.feed("task_failed", thing_id="task:7", kind="task", label="Сайт готовий.")
    attention.tick(3600)
    path = tmp_path / "attention.json"
    attention.save(path)

    again = Attention.load(path)
    assert again.t == attention.t
    assert again.state() == attention.state()
    assert len(again.wants) == len(attention.wants)
    assert len(again.things) == len(attention.things)


def test_a_missing_file_gives_a_new_attention(tmp_path):
    attention = Attention.load(tmp_path / "nothing.json")
    assert attention.t == 0.0
    assert attention.wants == []


def test_a_broken_file_gives_a_new_attention(tmp_path):
    path = tmp_path / "attention.json"
    path.write_text("{ not json", encoding="utf-8")
    assert Attention.load(path).t == 0.0


def test_a_file_of_another_version_gives_a_new_attention(tmp_path):
    path = tmp_path / "attention.json"
    path.write_text(json.dumps({"version": 999, "t": 500.0}), encoding="utf-8")
    assert Attention.load(path).t == 0.0


def test_v1_state_migrates_to_verbs(tmp_path):
    path = tmp_path / "attention.json"
    path.write_text(json.dumps({
        "version": 1,
        "t": 120.0,
        "needs": [
            {"id": 1, "drive": "closure", "about": "task:7", "label": "сайт", "level": 0.6},
            {"id": 2, "drive": "competence", "about": "task:9", "label": "деплой", "level": 0.4},
            {"id": 3, "drive": "connection", "about": "self", "label": "", "level": 0.05},
        ],
    }), encoding="utf-8")
    attention = Attention.load(path)
    assert attention.t == 120.0
    verbs = {w.verb for w in attention.wants}
    assert verbs == {"finish", "fix"}  # self dropped, closure→finish, competence→fix
    assert {w.thing_id for w in attention.wants} == {"task:7", "task:9"}


def test_unknown_fields_are_dropped_and_missing_take_defaults(tmp_path):
    path = tmp_path / "attention.json"
    path.write_text(json.dumps({
        "version": 2,
        "t": 120.0,
        "something_new": "ignore me",
        "wants": [{"verb": "finish", "thing_id": "x", "care": 0.5, "extra": 1}],
    }), encoding="utf-8")
    attention = Attention.load(path)
    assert attention.t == 120.0
    assert attention.wants[0].care == 0.5
    assert attention.wants[0].pressing == 0.0
    assert attention.wants[0].said == 0


def test_the_engine_saves_the_attention_as_it_turns(tmp_path):
    path = tmp_path / "attention.json"
    clock = Clock()
    motor = Engine(Intents(tmp_path / "intents.json"), speak=lambda m: None,
                   now=clock, quiet=("00:00", "00:00"), daily_limit=99,
                   attention=Attention(), attention_path=path)
    motor.note(event())
    clock.go(3600)
    motor.note(tick())
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 2


def test_a_restart_keeps_the_pressure(tmp_path):
    path = tmp_path / "attention.json"
    store = Intents(tmp_path / "intents.json")
    clock = Clock()
    first = Engine(store, speak=lambda m: None, now=clock, quiet=("00:00", "00:00"),
                   daily_limit=99, attention=Attention(), attention_path=path)
    first.note(event())
    clock.go(3600)
    first.note(tick())
    before = Attention.load(path).state()

    second = Engine(store, speak=lambda m: None, now=clock, quiet=("00:00", "00:00"),
                    daily_limit=99, attention_path=path)
    assert second._attention is not None
    assert second._attention.state() == before


def test_without_a_path_nothing_is_written(tmp_path):
    clock = Clock()
    motor = Engine(Intents(tmp_path / "intents.json"), speak=lambda m: None,
                   now=clock, quiet=("00:00", "00:00"), daily_limit=99, attention=Attention())
    motor.note(event())
    clock.go(3600)
    motor.note(tick())
    assert list(Path(tmp_path).glob("attention*")) == []


def test_a_legacy_soma_file_wakes_as_attention(tmp_path):
    """The file was ``soma.json`` before the rename; a live brain keeps it."""
    legacy = tmp_path / "soma.json"
    attention = Attention()
    attention.feed("task_failed", thing_id="task:7", kind="task", label="Сайт готовий.")
    attention.tick(3600)
    attention.save(legacy)

    motor = Engine(
        Intents(tmp_path / "intents.json"),
        attention_path=tmp_path / "attention.json",
        now=Clock(),
        quiet=("00:00", "00:00"),
        daily_limit=0,
    )
    assert motor._attention is not None
    assert [want.verb for want in motor._attention.wants] == ["finish"]
    motor._save_attention(motor._now())
    assert (tmp_path / "attention.json").is_file()
