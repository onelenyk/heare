"""The engine with a attention: what the attention feeds, and how it becomes an intent."""

from __future__ import annotations

from dataclasses import dataclass

from heare.engine import Engine, Intents
from heare.engine.attention import Attention
from heare.engine.intent import ATTENTION, tag


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


def engine(tmp_path, *, attention: Attention | None = None):
    store = Intents(tmp_path / "intents.json")
    said: list = []
    clock = Clock()
    motor = Engine(
        store, speak=said.append, now=clock,
        quiet=("00:00", "00:00"), daily_limit=99, attention=attention,
    )
    return store, said, motor, clock


def attention_intents(store, clock):
    return [i for i in store.alive(clock()) if i.about.startswith("soma:")]


def test_without_a_attention_the_result_is_said_at_once(tmp_path):
    _, said, motor, _ = engine(tmp_path)
    motor.note(event("done"))
    assert len(said) == 1


def test_with_a_attention_the_news_is_still_said_at_once(tmp_path):
    _, said, motor, _ = engine(tmp_path, attention=Attention())
    motor.note(event("done"))
    assert len(said) == 1


def test_a_failure_makes_a_attention_intent(tmp_path):
    store, _, motor, clock = engine(tmp_path, attention=Attention())
    motor.note(event("failed"))
    assert any(i.why == "soma" and i.about == "soma:7" for i in store._by_id.values())
    attention = motor._attention
    assert any(w.thing_id == "7" and w.care > 0 for w in attention.wants)


def test_a_finished_task_leaves_nothing_pressing(tmp_path):
    store, _, motor, clock = engine(tmp_path, attention=Attention())
    motor.note(event("done"))
    clock.go(3600)
    motor.note(tick())
    assert attention_intents(store, clock) == []


def test_the_same_need_is_not_held_twice(tmp_path):
    store, _, motor, clock = engine(tmp_path, attention=Attention())
    motor.note(event("failed"))
    clock.go(3600)
    motor.note(tick())
    clock.go(600)
    motor.note(tick())
    clock.go(600)
    motor.note(tick())
    assert len([i for i in store._by_id.values() if i.about == "soma:7"]) == 1


def test_speaking_does_not_make_an_intent(tmp_path):
    store, _, motor, clock = engine(tmp_path, attention=Attention())
    motor.note(Ev("heard", {}))
    clock.go(3600)
    motor.note(tick())
    assert store.alive(clock()) == []


def test_a_failed_task_keeps_pressing_after_the_news_is_stale(tmp_path):
    store, _, motor, clock = engine(tmp_path, attention=Attention())
    motor.note(event("failed"))
    clock.go(3 * 3600)
    motor.note(tick())
    alive = store.alive(clock())
    assert [i for i in alive if i.why == "task_failed"] == []
    attention = motor._attention
    assert any(w.thing_id == "7" and w.care > 0.0 for w in attention.wants)


def test_the_weight_stays_inside_what_the_form_allows(tmp_path):
    store, _, motor, clock = engine(tmp_path, attention=Attention())
    motor.note(event("failed"))
    attentions = [i for i in store._by_id.values() if i.why == "soma"]
    assert attentions
    assert 1 <= attentions[0].weight <= 3


def test_the_news_is_said_the_same_with_a_attention_and_without(tmp_path):
    _, said_body, motor_body, _ = engine(tmp_path, attention=Attention())
    motor_body.note(event("done"))
    _, said_plain, motor_plain, _ = engine(tmp_path)
    motor_plain.note(event("done"))
    assert len(said_body) == len(said_plain) == 1
    assert said_body[0].text == said_plain[0].text


def test_its_own_remark_is_not_the_person_answering(tmp_path):
    store, said, motor, clock = engine(tmp_path, attention=Attention())
    motor.note(event("failed"))
    assert said, "the remark has to go out first"
    gap = motor._gap_s
    clock.go(60)
    motor.note(Ev("heard", {"text": said[0].text, "channel": "engine", "person": False}))
    assert motor._answered is False, "the engine counted its own echo as an answer"
    assert motor._gap_s == gap, "the gap narrowed on its own echo"


def test_the_person_answering_narrows_the_gap(tmp_path):
    store, said, motor, clock = engine(tmp_path, attention=Attention())
    motor.note(event("failed"))
    gap = motor._gap_s
    clock.go(60)
    motor.note(Ev("heard", {"text": "дякую", "channel": "voice", "person": True}))
    assert motor._answered is True
    assert motor._gap_s < gap


def test_an_attention_remark_is_not_a_work_result(tmp_path):
    from heare.core.conversation import RESULT_PREFIX
    from heare.engine.engine import ON_MIND_PREFIX

    store, said, motor, clock = engine(tmp_path, attention=Attention())
    motor.note(event("failed"))
    clock.go(3600)
    motor.note(tick())
    on_mind = [message for message in said if ON_MIND_PREFIX in message.text]
    assert on_mind, "attention's remark was never said"
    assert RESULT_PREFIX not in on_mind[0].text
    assert "зроби сайт" in on_mind[0].text


def test_dropping_by_hand_is_not_speaking(tmp_path):
    store, said, motor, clock = engine(tmp_path, attention=Attention())
    motor.note(event("failed"))
    energy = motor._attention.energy
    held = [i for i in store.alive(clock()) if i.why == "soma"]
    assert held, "the want waits as an intent"
    assert motor.drop(held[0].id)
    assert motor._attention.energy == energy, "a drop cost the energy of a remark"


def test_plans_are_listed_and_two_are_merged(tmp_path):
    attention = Attention()
    attention.feed("plan_said", thing_id="plan:a", kind="promise", label="податки")
    attention.feed("plan_said", thing_id="plan:b", kind="promise", label="покдатки")
    store, said, motor, clock = engine(tmp_path, attention=attention)

    assert {plan["id"] for plan in motor.plans()} == {"plan:a", "plan:b"}
    store.hold("покдатки", ATTENTION, 2, about=tag(ATTENTION, "plan:b"))

    assert motor.merge_plans("plan:a", "plan:b") is True
    assert {plan["id"] for plan in motor.plans()} == {"plan:a"}
    assert not [i for i in store.alive(clock()) if i.about == tag(ATTENTION, "plan:b")]
