"""English memory: dates, the recall answer, search, and the prompts.

Every case sets the language explicitly and puts it back afterwards. The
default stays ``uk``; what is checked here is the other path, plus the
Ukrainian one it must not have cost.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from heare.core import i18n
from heare.memory import review, search
from heare.memory.consolidate import consolidate
from heare.memory.contracts import PENDING
from heare.memory.fts import _sanitize_fts_query
from heare.memory.journal import Journal
from heare.memory.persist import SpinePersistence
from heare.memory.search import Fragment
from heare.memory.when import Span, parse_when, said_when, strip_when

from tests.heare.fake_facts import FakeFacts

# Friday, 21 August 2026 — the same day as the Ukrainian table, so a
# weekday is never accidentally today.
NOW = datetime(2026, 8, 21, 14, 30)


@pytest.fixture(autouse=True)
def english():
    i18n.choose(lambda: "en")
    yield
    i18n.choose(lambda: "uk")


def days(span: Span) -> tuple[str, str]:
    return (
        datetime.fromtimestamp(span.start).strftime("%Y-%m-%d"),
        datetime.fromtimestamp(span.end).strftime("%Y-%m-%d"),
    )


def when(text: str) -> Span:
    span = parse_when(text, now=NOW)
    assert span is not None, f"nothing parsed out of {text!r}"
    return span


@pytest.mark.parametrize(
    "text, first, last",
    [
        ("today", "2026-08-21", "2026-08-22"),
        ("yesterday", "2026-08-20", "2026-08-21"),
        ("the day before yesterday", "2026-08-19", "2026-08-20"),
        ("tomorrow at 9", "2026-08-22", "2026-08-23"),
        ("two hours ago", "2026-08-21", "2026-08-22"),
        ("three days ago", "2026-08-18", "2026-08-19"),
        ("two weeks ago", "2026-08-03", "2026-08-10"),
        ("last week", "2026-08-10", "2026-08-17"),
        ("this week", "2026-08-17", "2026-08-24"),
        ("last weekend", "2026-08-15", "2026-08-17"),
        ("this weekend", "2026-08-22", "2026-08-24"),
        ("last month", "2026-07-01", "2026-08-01"),
        ("this month", "2026-08-01", "2026-09-01"),
        ("last year", "2025-01-01", "2026-01-01"),
        ("this year", "2026-01-01", "2027-01-01"),
        ("in March", "2026-03-01", "2026-04-01"),
        ("in October", "2025-10-01", "2025-11-01"),
        ("on Tuesday", "2026-08-18", "2026-08-19"),
        ("on Friday", "2026-08-21", "2026-08-22"),
        ("on 12 March", "2026-03-12", "2026-03-13"),
        ("on 12 October", "2025-10-12", "2025-10-13"),
        ("on 12 October 2026", "2026-10-12", "2026-10-13"),
        ("October 12", "2025-10-12", "2025-10-13"),
    ],
)
def test_english_time_expressions_are_day_ranges(
    text: str, first: str, last: str
) -> None:
    assert days(when(text)) == (first, last)


def test_may_is_a_month_only_when_it_is_one() -> None:
    assert parse_when("it may work", now=NOW) is None
    assert days(when("in May")) == ("2026-05-01", "2026-06-01")


def test_an_english_question_without_a_time_parses_to_nothing() -> None:
    assert parse_when("what did I say about the timeout", now=NOW) is None


def test_the_english_time_expression_leaves_the_query() -> None:
    span = when("what did I say yesterday about the timeout")
    assert strip_when("what did I say yesterday about the timeout", span) == (
        "what did i say about the timeout"
    )


@pytest.mark.parametrize(
    "ts, said",
    [
        (datetime(2026, 8, 21, 9, 0), "today"),
        (datetime(2026, 8, 20, 9, 0), "yesterday"),
        (datetime(2026, 8, 19, 9, 0), "the day before yesterday"),
        (datetime(2026, 8, 18, 9, 0), "Tuesday"),
        (datetime(2026, 8, 14, 9, 0), "14 August"),
        (datetime(2025, 12, 30, 9, 0), "30 December 2025"),
    ],
)
def test_english_when_something_was_said(ts: datetime, said: str) -> None:
    assert said_when(ts.timestamp(), now=NOW) == said


def test_english_recall_answer_names_the_day_and_who_spoke() -> None:
    said = Fragment(
        ts=datetime(2026, 8, 21, 9, 0).timestamp(),
        text="The deployment finished early.",
        agent_spoken=False,
    )
    came_up = Fragment(
        ts=datetime(2026, 8, 21, 9, 0).timestamp(),
        text="The deployment finished early.",
        agent_spoken=True,
    )
    assert search.spoken([said], now=NOW) == (
        "Today you said: The deployment finished early."
    )
    assert search.spoken([came_up], now=NOW) == (
        "Today it came up: The deployment finished early."
    )


def test_english_recall_answers_silence_and_names_other_days() -> None:
    assert search.spoken([], now=NOW) == "I don't remember talking about that."

    fragments = [
        Fragment(
            ts=datetime(2026, 8, 21, 9, 0).timestamp(),
            text="The deployment finished early.",
            agent_spoken=False,
        ),
        Fragment(
            ts=datetime(2026, 8, 18, 9, 0).timestamp(),
            text="The release was cut on time.",
            agent_spoken=False,
        ),
    ]
    assert search.spoken(fragments, now=NOW) == (
        "Today you said: The deployment finished early. Also about that: Tuesday."
    )


def test_ukrainian_recall_is_unchanged() -> None:
    i18n.choose(lambda: "uk")
    assert search.spoken([], now=NOW) == "Не пригадую, щоб ми про це говорили."

    fragments = [
        Fragment(
            ts=datetime(2026, 8, 20, 9, 0).timestamp(),
            text="Розгортання завершилося вчасно.",
            agent_spoken=False,
        ),
        Fragment(
            ts=datetime(2026, 8, 18, 9, 0).timestamp(),
            text="Розгортання проходило вночі.",
            agent_spoken=False,
        ),
    ]
    assert search.spoken(fragments, now=NOW) == (
        "Вчора ти казав: Розгортання завершилося вчасно. Ще про це було у вівторок."
    )


def test_english_declension_and_inflection_reach_the_transcript(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(search, "NOT_YET_A_MEMORY_S", 0.0)
    store = SpinePersistence(tmp_path / "heare.db")
    try:
        store.log_user_turn("The deployment finished early.")
        store.log_user_turn("We talked about the backup schedule.")
        now = datetime.now()

        found = search.find(store, "what did I say about deployments", now=now)
        assert [fragment.text for fragment in found] == [
            "The deployment finished early."
        ]

        found = search.find(store, "what did I say about backup", now=now)
        assert [fragment.text for fragment in found] == [
            "We talked about the backup schedule."
        ]
    finally:
        store.close()


def test_english_query_is_stemmed_for_fts5() -> None:
    assert _sanitize_fts_query("deployments") == (
        '"deployments" OR "deployments"* OR "deployment"*'
    )
    assert '"deploy"*' in (_sanitize_fts_query("deployed") or "")
    assert '"run"*' in (_sanitize_fts_query("running") or "")


def test_ukrainian_declension_still_reaches_the_transcript(
    tmp_path, monkeypatch
) -> None:
    i18n.choose(lambda: "uk")
    monkeypatch.setattr(search, "NOT_YET_A_MEMORY_S", 0.0)
    store = SpinePersistence(tmp_path / "heare.db")
    try:
        store.log_user_turn("Ми говорили про собаку.")
        found = search.find(store, "що я казав про собака", now=datetime.now())
        assert [fragment.text for fragment in found] == ["Ми говорили про собаку."]
    finally:
        store.close()


def test_ukrainian_dates_are_still_ukrainian() -> None:
    i18n.choose(lambda: "uk")
    assert days(when("вчора")) == ("2026-08-20", "2026-08-21")
    assert said_when(datetime(2026, 8, 20, 9, 0).timestamp(), now=NOW) == "вчора"


def test_english_consolidation_keeps_english_facts(tmp_path) -> None:
    journal = Journal(tmp_path / "events")
    journal.append(
        "heard",
        {"text": "My name is Nazar.", "channel": "voice"},
        source="voice",
        trust="user_said",
        at=900.0,
    )
    journal.append(
        "heard",
        {"text": "I live in Berlin.", "channel": "voice"},
        source="voice",
        trust="user_said",
        at=900.0,
    )
    store = FakeFacts()

    report = consolidate(store, journal, now=1000.0)

    assert report.added == 2
    facts = store.current(include_pending=True)
    assert {(fact.predicate, fact.value, fact.text) for fact in facts} == {
        ("name", "Nazar", "Your name is Nazar."),
        ("lives_in", "Berlin", "You live in Berlin."),
    }
    assert all(fact.status == PENDING for fact in facts)


def test_the_review_prompt_speaks_english() -> None:
    assert "Grusha is my cat" in review._SYSTEM
    assert not any("\u0400" <= char <= "\u04ff" for char in review._SYSTEM)
