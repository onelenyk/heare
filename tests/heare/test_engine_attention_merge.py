"""Two entries that are one unfinished thing are folded into one."""

from __future__ import annotations

from heare.engine.attention import Attention


def test_merge_keeps_one_thing_and_the_stronger_numbers():
    attention = Attention()
    attention.feed("plan_said", thing_id="plan:a", kind="promise", label="податки")
    attention.tick(600)
    attention.feed("plan_said", thing_id="plan:b", kind="promise", label="покдатки")
    attention.tick(600)
    attention.spoke("plan:b")

    assert attention.merge("plan:a", "plan:b") is True
    assert [thing.id for thing in attention.things] == ["plan:a"]
    assert [want.thing_id for want in attention.wants] == ["plan:a"]
    want = attention.wants[0]
    assert want.said == 1
    assert attention.last_spoken == "plan:a"


def test_merge_refuses_a_missing_same_or_finished_thing():
    attention = Attention()
    attention.feed("plan_said", thing_id="plan:a", kind="promise", label="податки")
    assert attention.merge("plan:a", "plan:a") is False
    assert attention.merge("plan:a", "plan:missing") is False
    assert attention.merge("plan:missing", "plan:a") is False


def test_merge_writes_the_label_it_is_given():
    attention = Attention()
    attention.feed("plan_said", thing_id="plan:a", kind="promise", label="покдатки")
    attention.feed("plan_said", thing_id="plan:b", kind="promise", label="податки")
    assert attention.merge("plan:a", "plan:b", "податки до 19 листопада") is True
    assert attention.thing("plan:a").label == "податки до 19 листопада"
