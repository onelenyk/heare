"""One day of conversation becomes pending facts, never confirmed ones."""

from __future__ import annotations

import json
import os
import stat
from datetime import date, datetime, timedelta

import pytest

from heare.brain import next_review_at
from heare.memory.contracts import (
    CONSOLIDATED,
    MODEL_INFERRED,
    PENDING,
    USER_SAID,
    Fact,
    ReviewReport,
)
from heare.memory.journal import Journal
from heare.memory.review import (
    MAX_MESSAGES,
    last_reviewed,
    mark_reviewed,
    review_day,
)
from tests.heare.fake_facts import FakeFacts
from tests.heare.fakes import Script

DAY = date(2026, 5, 1)


def _stamp(day: date, hour: int) -> float:
    return datetime(day.year, day.month, day.day, hour, 0).timestamp()


def _heard(journal: Journal, text: str, at: float) -> dict:
    return journal.append(
        "heard",
        {"text": text, "channel": "voice"},
        source="voice",
        trust="user_said",
        at=at,
    )


def _journal_with_day(tmp_path) -> Journal:
    journal = Journal(tmp_path / "events")
    _heard(journal, "Мене звати Назар.", at=_stamp(DAY, 10))
    _heard(journal, "Я люблю каву.", at=_stamp(DAY, 11))
    journal.append(
        "said",
        {"text": "Я люблю дощ.", "channel": "voice"},
        source="engine",
        trust="system",
        at=_stamp(DAY, 12),
    )
    _heard(
        journal, "Це було вчора.", at=_stamp(DAY - timedelta(days=1), 12)
    )
    return journal


def _known() -> FakeFacts:
    store = FakeFacts()
    store.add(Fact(
        subject="owner",
        predicate="name",
        value="Nazar",
        text="Your name is Nazar.",
        source=CONSOLIDATED,
        trust=USER_SAID,
        status=PENDING,
        valid_from=0.0,
        source_ids=("seed",),
        created_at=1.0,
    ))
    return store


def _model() -> Script:
    answer = json.dumps([
        {
            "predicate": "likes_coffee",
            "value": "strong coffee",
            "text": "You like strong coffee.",
            "confidence": 0.9,
        },
        {
            "predicate": "name",
            "value": "Nazar",
            "text": "Your name is Nazar.",
        },
        {"predicate": "Bad Predicate", "value": "x", "text": "no"},
    ])
    model = Script()
    model.will_say(f"```json\n{answer}\n```")
    return model


async def test_a_review_adds_only_the_new_valid_fact(tmp_path):
    store, model = _known(), _model()

    report = await review_day(
        store, _journal_with_day(tmp_path), model, day=DAY, now=1000.0
    )

    assert report == ReviewReport(considered=2, added=1, skipped=1)
    facts = store.current(include_pending=True)
    review = [f for f in facts if f.source_ids[0].startswith("review:")]
    assert len(review) == 1
    fact = review[0]
    assert (fact.subject, fact.predicate, fact.value) == (
        "owner", "likes_coffee", "strong coffee"
    )
    assert fact.text == "You like strong coffee."
    assert fact.source == CONSOLIDATED
    assert fact.trust == MODEL_INFERRED
    assert fact.status == PENDING
    assert fact.confidence == 0.9
    assert fact.valid_from == fact.created_at == 1000.0
    assert fact.source_ids[0].startswith("review:")

    assert store.current() == []
    assert len(facts) == 2

    messages = model.asked[0]
    assert messages[0]["role"] == "system"
    assert "durable" in messages[0]["content"]
    assert "user: Мене звати Назар." in messages[1]["content"]
    assert "user: Я люблю каву." in messages[1]["content"]
    assert "user: Це було вчора." not in messages[1]["content"]
    assert "user: Я люблю дощ." not in messages[1]["content"]
    assert model.offered == [[]]


async def test_a_second_review_adds_nothing(tmp_path):
    journal, store = _journal_with_day(tmp_path), _known()
    model = _model()
    first = await review_day(store, journal, model, day=DAY, now=1000.0)
    source = next(
        fact.source_ids[0]
        for fact in store.current(include_pending=True)
        if fact.source_ids[0].startswith("review:")
    )
    before = len(store.current(include_pending=True))

    model = _model()
    again = await review_day(store, journal, model, day=DAY, now=2000.0)

    assert first.added == 1
    assert again.added == 0
    assert again.skipped == 2
    assert store.has_source(source)
    assert len(store.current(include_pending=True)) == before


async def test_a_day_without_heard_events_does_not_ask_the_model(tmp_path):
    journal = Journal(tmp_path / "events")
    _heard(journal, "Це було вчора.", at=_stamp(DAY - timedelta(days=1), 12))
    model = Script()

    report = await review_day(journal=journal, store=FakeFacts(), model=model,
                              day=DAY, now=1000.0)

    assert report == ReviewReport()
    assert model.asked == []


async def test_malformed_json_is_an_error_and_adds_nothing(tmp_path):
    store = FakeFacts()
    model = Script()
    model.will_say("Не знаю, що сказати.")

    report = await review_day(
        store, _journal_with_day(tmp_path), model, day=DAY, now=1000.0
    )

    assert report.considered == 2
    assert report.error
    assert report.added == 0
    assert store.current(include_pending=True) == []


def test_next_review_is_today_when_the_hour_is_ahead():
    now = _stamp(DAY, 3)

    when = next_review_at(now, 4)

    assert when == _stamp(DAY, 4)
    assert datetime.fromtimestamp(when).hour == 4


def test_next_review_is_tomorrow_when_the_hour_has_passed():
    now = _stamp(DAY, 5)

    when = next_review_at(now, 4)

    assert when == _stamp(DAY + timedelta(days=1), 4)
    assert datetime.fromtimestamp(when).hour == 4


def test_next_review_is_tomorrow_at_the_exact_hour():
    now = _stamp(DAY, 4)

    assert next_review_at(now, 4) == _stamp(DAY + timedelta(days=1), 4)


def test_the_review_marker_round_trips_privately(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX modes")
    root = tmp_path / "memory"

    assert last_reviewed(root) is None
    mark_reviewed(root, DAY)

    assert last_reviewed(root) == DAY
    assert json.loads((root / "review.json").read_text(encoding="utf-8")) == {
        "day": DAY.isoformat()
    }
    assert stat.S_IMODE((root / "review.json").stat().st_mode) == 0o600
    assert stat.S_IMODE(root.stat().st_mode) == 0o700


def test_a_broken_review_marker_reads_as_never_reviewed(tmp_path):
    root = tmp_path / "memory"
    root.mkdir()
    path = root / "review.json"

    path.write_text("{not json", encoding="utf-8")
    assert last_reviewed(root) is None

    path.write_text(json.dumps({"day": "not-a-date"}), encoding="utf-8")
    assert last_reviewed(root) is None

    path.write_text(json.dumps(["2026-05-01"]), encoding="utf-8")
    assert last_reviewed(root) is None


async def test_a_late_fact_survives_the_message_cap(tmp_path):
    journal = Journal(tmp_path / "events")
    for index in range(MAX_MESSAGES + 1):
        _heard(journal, f"Ранок {index}.", at=_stamp(DAY, 9))
    _heard(
        journal, "Мій улюблений колір — синій.",
        at=_stamp(DAY, 22),
    )
    model = Script()
    model.will_say("[]")

    report = await review_day(
        FakeFacts(), journal, model, day=DAY, now=_stamp(DAY, 23)
    )

    assert report.considered == MAX_MESSAGES
    prompt = model.asked[0][1]["content"]
    assert "user: Мій улюблений колір — синій." in prompt
    assert prompt.rstrip().endswith("user: Мій улюблений колір — синій.")
    assert "user: Ранок 0." not in prompt


async def test_a_late_fact_survives_the_character_cap(tmp_path):
    journal = Journal(tmp_path / "events")
    filler = "а" * 100
    total = 65
    for index in range(total):
        _heard(journal, f"{index:03d} {filler}", at=_stamp(DAY, 9))
    _heard(
        journal, "Мій улюблений колір — синій.",
        at=_stamp(DAY, 22),
    )
    model = Script()
    model.will_say("[]")

    report = await review_day(
        FakeFacts(), journal, model, day=DAY, now=_stamp(DAY, 23)
    )

    assert 0 < report.considered < total
    prompt = model.asked[0][1]["content"]
    assert "user: Мій улюблений колір — синій." in prompt
    assert prompt.rstrip().endswith("user: Мій улюблений колір — синій.")
