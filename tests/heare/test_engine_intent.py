"""The intent: still worth saying, or not."""

from __future__ import annotations

from heare.engine import Intent


def intent(**over) -> Intent:
    base = {"id": 1, "what": "детектор звуку готовий", "why": "task_done", "weight": 2}
    base.update(over)
    return Intent(**base)


def test_a_fresh_intent_is_alive():
    assert intent().alive(now=100.0)


def test_an_intent_past_its_deadline_is_not_alive():
    assert not intent(until=50.0).alive(now=100.0)


def test_an_intent_with_no_deadline_never_expires():
    assert intent(until=None).alive(now=10_000_000.0)


def test_a_delivered_intent_is_not_alive():
    assert not intent(status="delivered").alive(now=100.0)


def test_a_dropped_intent_is_not_alive():
    assert not intent(status="dropped").alive(now=100.0)
