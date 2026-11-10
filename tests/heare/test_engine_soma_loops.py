"""A plan said out loud: attention catches it and brings it back later.

The smallest live wire: the person says «завтра треба подзвонити мамі» in
conversation, the words are theirs, and attention holds them. No model call,
no new event in the core — the detector is handed to the engine by the
assembler, the way the core is handed its language.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from heare.engine import Engine, Intents, Soma
from heare.language import en, uk


@dataclass
class Ev:
    kind: str
    data: dict = field(default_factory=dict)


class Clock:
    def __init__(self, moment: float = 1000.0) -> None:
        self.moment = moment

    def __call__(self) -> float:
        return self.moment

    def go(self, seconds: float) -> None:
        self.moment += seconds


def engine(tmp_path, *, loop=uk.open_loop, daily_limit=1):
    store = Intents(tmp_path / "intents.json")
    said: list = []
    clock = Clock()
    motor = Engine(store, speak=said.append, now=clock, quiet=("00:00", "00:00"),
                   daily_limit=daily_limit, soma=Soma(), open_loop=loop)
    return store, said, motor, clock


def heard(text: str) -> Ev:
    return Ev("heard", {"text": text, "channel": "voice"})


def test_a_plan_is_caught_in_the_persons_own_words():
    assert uk.open_loop("завтра треба подзвонити мамі") == "завтра треба подзвонити мамі"
    assert uk.open_loop("нагадай мені про лікаря") == "нагадай мені про лікаря"
    assert en.open_loop("I need to call mom tomorrow") == "I need to call mom tomorrow"


def test_a_question_to_the_assistant_is_not_a_plan():
    assert uk.open_loop("що мені треба зробити?") is None
    assert uk.open_loop("яка погода завтра?") is None


def test_small_talk_is_not_a_plan():
    assert uk.open_loop("привіт, як справи") is None
    assert en.open_loop("nice weather") is None


def test_a_plan_becomes_a_thing_attention_holds(tmp_path):
    store, said, motor, clock = engine(tmp_path)
    motor.note(heard("завтра треба подзвонити мамі"))

    want = next(w for w in motor._soma.wants if w.verb == "finish")
    assert want.care > 0 and want.pressing >= 0.5
    assert store.alive(clock()) == [], "a plan is not brought back in the same breath"
    clock.go(11 * 60)
    held = [i for i in store.alive(clock()) if i.what == "завтра треба подзвонити мамі"]
    assert held, "the plan waits as an intent"
    assert "завтра треба подзвонити мамі" in motor.mood()


def test_the_same_plan_twice_is_one_thing(tmp_path):
    store, said, motor, clock = engine(tmp_path, daily_limit=0)
    motor.note(heard("треба подзвонити мамі"))
    motor.note(heard("Треба подзвонити мамі"))
    assert len([w for w in motor._soma.wants if w.verb == "finish"]) == 1


def test_without_a_detector_nothing_is_caught(tmp_path):
    store, said, motor, clock = engine(tmp_path, loop=None)
    motor.note(heard("завтра треба подзвонити мамі"))
    assert not [w for w in motor._soma.wants if w.verb == "finish"]


def test_the_engine_does_not_hear_its_own_remark(tmp_path):
    """The delivery comes back as ``heard``; it is not the person speaking."""
    store, said, motor, clock = engine(tmp_path)
    motor.note(Ev("heard", {
        "text": "Нагадування: завтра треба подзвонити мамі",
        "channel": "engine", "person": False,
    }))
    assert not [w for w in motor._soma.wants if w.verb == "finish"]
    assert said == []


def test_a_work_result_is_not_a_plan(tmp_path):
    from heare.core.conversation import RESULT_PREFIX

    store, said, motor, clock = engine(tmp_path)
    motor.note(Ev("heard", {
        "text": f"{RESULT_PREFIX}\n— завтра треба подзвонити мамі",
        "channel": "engine", "person": False,
    }))
    assert not [w for w in motor._soma.wants if w.verb == "finish"]


def test_a_negated_need_is_not_a_plan():
    assert uk.open_loop("не треба цього робити") is None
    assert uk.open_loop("мені не потрібно нічого") is None


def test_a_statement_about_tomorrow_is_not_a_plan():
    assert uk.open_loop("завтра буде дощ") is None
    assert uk.open_loop("завтра я на конференції") is None
    assert en.open_loop("tomorrow it will rain") is None


def test_a_plain_future_is_not_a_plan():
    assert en.open_loop("I'll be there in five") is None
    assert en.open_loop("it will be fine") is None


def test_a_waiting_plan_is_not_held_again(tmp_path):
    store, said, motor, clock = engine(tmp_path, daily_limit=0)
    motor.note(heard("треба подзвонити мамі"))
    clock.go(5 * 60)
    motor.note(Ev("tick"))
    clock.go(5 * 60)
    motor.note(Ev("tick"))
    plans = [i for i in store._by_id.values() if i.about.startswith("soma:plan:")]
    assert len(plans) == 1, f"the same plan was held {len(plans)} times"
