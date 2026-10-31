"""Conversation becomes confirmed facts the owner can see and forget."""

from __future__ import annotations

import asyncio
import json
import os
import stat
from datetime import date, datetime, timedelta

import pytest

from heare.brain import next_review_at, next_review_day
from heare.memory.book import Book
from heare.memory.contracts import (
    CONFIRMED,
    CONSOLIDATED,
    MODEL_INFERRED,
    PENDING,
    USER_SAID,
    Fact,
    ReviewReport,
)
from heare.memory.facts import SQLiteFacts
from heare.memory.journal import Journal
from heare.memory.review import (
    MAX_MESSAGES,
    last_reviewed,
    mark_reviewed,
    notice,
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
    assert fact.status == CONFIRMED
    assert fact.confidence == 0.9
    assert fact.valid_from == fact.created_at == 1000.0
    assert fact.source_ids[0].startswith("review:")

    assert [f.predicate for f in store.current()] == ["likes_coffee"]
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


def _pet_answer() -> str:
    return json.dumps([{
        "predicate": "pet",
        "value": "Груша, кіт",
        "text": "У тебе є кіт Груша.",
        "confidence": 0.9,
    }])


async def test_notice_learns_a_fact_and_does_not_reread_it(tmp_path):
    journal = Journal(tmp_path / "events")
    _heard(journal, "Груша мій кіт.", at=_stamp(DAY, 10))
    store = FakeFacts()
    state_path = tmp_path / "memory" / "consolidate.json"
    model = Script()
    model.will_say(_pet_answer())

    first = await notice(
        store, journal, model, state_path=state_path, now=1000.0
    )

    assert first == ReviewReport(considered=1, added=1)
    assert len(model.asked) == 1
    assert "user: Груша мій кіт." in model.asked[0][1]["content"]
    facts = store.current()
    assert [(f.predicate, f.value, f.status) for f in facts] == [
        ("pet", "Груша, кіт", CONFIRMED)
    ]
    assert state_path.exists()

    again_model = Script()
    again = await notice(
        store, journal, again_model, state_path=state_path, now=2000.0
    )

    assert again == ReviewReport()
    assert again_model.asked == []
    assert len(store.current()) == 1


async def test_notice_reads_only_the_lines_heard_since_the_last_pass(tmp_path):
    journal = Journal(tmp_path / "events")
    _heard(journal, "Груша мій кіт.", at=_stamp(DAY, 10))
    store = FakeFacts()
    state_path = tmp_path / "memory" / "consolidate.json"
    first_model = Script()
    first_model.will_say("[]")
    await notice(store, journal, first_model, state_path=state_path, now=1.0)

    _heard(journal, "Я люблю каву.", at=_stamp(DAY, 11))
    model = Script()
    model.will_say("[]")
    report = await notice(
        store, journal, model, state_path=state_path, now=2.0
    )

    assert report == ReviewReport(considered=1)
    prompt = model.asked[0][1]["content"]
    assert "user: Я люблю каву." in prompt
    assert "user: Груша мій кіт." not in prompt


async def test_notice_does_not_ask_the_model_for_a_said_line(tmp_path):
    journal = Journal(tmp_path / "events")
    journal.append(
        "said",
        {"text": "Я люблю дощ.", "channel": "voice"},
        source="engine",
        trust="system",
        at=_stamp(DAY, 12),
    )
    model = Script()

    report = await notice(
        FakeFacts(), journal, model,
        state_path=tmp_path / "consolidate.json", now=1000.0,
    )

    assert report == ReviewReport()
    assert model.asked == []


async def test_a_failed_notice_keeps_the_cursor_for_the_next_pass(tmp_path):
    journal = Journal(tmp_path / "events")
    _heard(journal, "Груша мій кіт.", at=_stamp(DAY, 10))
    store = FakeFacts()
    state_path = tmp_path / "consolidate.json"
    broken = Script()
    broken.will_say("Не знаю, що сказати.")

    failed = await notice(
        store, journal, broken, state_path=state_path, now=1000.0
    )

    assert failed.error
    assert failed.added == 0
    assert store.current(include_pending=True) == []
    assert not state_path.exists()

    model = Script()
    model.will_say(_pet_answer())
    retry = await notice(
        store, journal, model, state_path=state_path, now=2000.0
    )

    assert retry == ReviewReport(considered=1, added=1)
    assert "user: Груша мій кіт." in model.asked[0][1]["content"]
    assert len(store.current()) == 1


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


def test_next_review_day_without_a_marker_is_yesterday():
    today = date(2026, 5, 10)

    assert next_review_day(None, today) == date(2026, 5, 9)


def test_next_review_day_moves_one_day_forward():
    today = date(2026, 5, 10)

    assert next_review_day(date(2026, 5, 7), today) == date(2026, 5, 8)


def test_next_review_day_after_yesterday_is_none():
    today = date(2026, 5, 10)

    assert next_review_day(date(2026, 5, 9), today) is None


def test_next_review_day_caps_the_catch_up():
    today = date(2026, 5, 10)

    assert next_review_day(date(2026, 4, 1), today) == date(2026, 5, 3)


def test_next_review_day_with_a_future_marker_is_none():
    today = date(2026, 5, 10)

    assert next_review_day(date(2026, 5, 11), today) is None


async def test_review_once_reviews_yesterday_and_marks_it(tmp_path, monkeypatch):
    from heare.brain import Brain
    from heare.config import Settings

    brain = object.__new__(Brain)
    brain.settings = Settings(memory_dir=tmp_path, review_hour=4)
    brain._review_lock = asyncio.Lock()
    asked: list[date | None] = []

    async def fake(day: date | None = None) -> ReviewReport:
        asked.append(day)
        return ReviewReport(considered=1, added=1)

    monkeypatch.setattr(brain, "review_now", fake)

    run = await brain.review_once()

    yesterday = date.today() - timedelta(days=1)
    assert run.day == yesterday
    assert run.report == ReviewReport(considered=1, added=1)
    assert run.already is False
    assert asked == [yesterday]
    assert last_reviewed(tmp_path) == yesterday
    again = await brain.review_once()
    assert again.already is True and again.report is None
    assert asked == [yesterday]


async def test_review_now_says_when_today_was_already_reviewed(tmp_path):
    from types import SimpleNamespace

    from heare.brain import Brain
    from heare.config import Settings
    from heare.core.i18n import _ as said
    from heare.features import build_features

    brain = object.__new__(Brain)
    brain.settings = Settings(memory_dir=tmp_path, review_hour=4)
    brain._review_lock = asyncio.Lock()
    brain.core = SimpleNamespace(
        state=SimpleNamespace(emit=lambda kind, **data: None))
    today = date.today()
    mark_reviewed(tmp_path, today)

    run = await brain.review_once()

    assert run.already is True
    assert run.report is None
    assert run.day == today

    features = build_features(brain)
    await features.act("review", "run", True)
    await features._running["review"]
    assert features.list()["features"][0]["note"] == said("Already reviewed today.")


async def test_a_failed_review_is_retried_and_not_marked(tmp_path, monkeypatch):
    from heare.brain import Brain
    from heare.config import Settings

    brain = object.__new__(Brain)
    brain.settings = Settings(memory_dir=tmp_path, review_hour=4)
    brain._review_lock = asyncio.Lock()
    asked: list[date | None] = []

    async def fake(day: date | None = None) -> ReviewReport:
        asked.append(day)
        return ReviewReport(error="boom")

    monkeypatch.setattr(brain, "review_now", fake)

    first = await brain.review_once()
    second = await brain.review_once()

    yesterday = date.today() - timedelta(days=1)
    assert first.report is not None and first.report.error == "boom"
    assert second.report is not None and second.report.error == "boom"
    assert asked == [yesterday, yesterday]
    assert last_reviewed(tmp_path) is None


async def test_conflicting_values_are_not_both_current(tmp_path):
    journal = Journal(tmp_path / "events")
    _heard(journal, "Я живу в Києві.", at=_stamp(DAY, 10))
    store = FakeFacts()
    first = Script()
    first.will_say(json.dumps([{
        "predicate": "lives_in",
        "value": "Kyiv",
        "text": "You live in Kyiv.",
        "confidence": 0.9,
    }]))
    await review_day(store, journal, first, day=DAY, now=1000.0)
    assert [fact.value for fact in store.current()] == ["Kyiv"]

    next_day = DAY + timedelta(days=1)
    _heard(journal, "Я живу в Берліні.", at=_stamp(next_day, 10))
    second = Script()
    second.will_say(json.dumps([{
        "predicate": "lives_in",
        "value": "Berlin",
        "text": "You live in Berlin.",
        "confidence": 0.9,
    }]))
    await review_day(store, journal, second, day=next_day, now=2000.0)

    assert [fact.value for fact in store.current()] == ["Berlin"]
    old = next(
        fact for fact in store.current(include_pending=True, now=1500.0)
        if fact.value == "Kyiv"
    )
    assert old.valid_to == 2000.0


async def test_confidence_zero_is_not_confirmed(tmp_path):
    journal = Journal(tmp_path / "events")
    _heard(journal, "Я живу в Києві.", at=_stamp(DAY, 10))
    db = tmp_path / "heare.db"
    facts = SQLiteFacts(db)
    model = Script()
    model.will_say(json.dumps([{
        "predicate": "lives_in",
        "value": "Kyiv",
        "text": "You live in Kyiv.",
        "confidence": 0.0,
    }]))

    report = await review_day(facts, journal, model, day=DAY, now=1000.0)

    assert report.added == 0
    assert facts.current(include_pending=True) == []
    book = Book(db, profile_dir=tmp_path / "memory", journal=journal, facts=facts)
    try:
        assert "Kyiv" not in await book.always()
    finally:
        book.close()
