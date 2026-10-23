"""One consolidation pass: heard words become pending facts."""

from __future__ import annotations

from heare.memory.consolidate import consolidate
from heare.memory.contracts import (
    CONSOLIDATED,
    PENDING,
    USER_SAID,
    ConsolidationReport,
)
from heare.memory.journal import Journal
from tests.heare.fake_facts import FakeFacts


def _heard(journal: Journal, text: str, at: float = 900.0) -> dict:
    return journal.append(
        "heard",
        {"text": text, "channel": "voice"},
        source="voice",
        trust="user_said",
        at=at,
    )


def _adding_pass(tmp_path):
    journal = Journal(tmp_path / "events")
    heard = [
        _heard(journal, "Мене звати Олена."),
        _heard(journal, "Я люблю каву."),
        _heard(journal, "Мені подобається джаз."),
        _heard(journal, "Я працюю над heare."),
        _heard(journal, "Я живу в Києві."),
        _heard(journal, "Не забути зателефонувати мамі."),
    ]
    noisy = _heard(journal, "Сьогодні гарна погода.")
    journal.append(
        "said",
        {"text": "Я люблю дощ.", "channel": "voice"},
        source="engine",
        trust="system",
        at=900.0,
    )
    store = FakeFacts()
    report = consolidate(store, journal, now=1000.0)
    return store, journal, report, heard, noisy


def test_heard_patterns_become_one_pending_fact_each(tmp_path):
    store, _, report, heard, _ = _adding_pass(tmp_path)

    assert report == ConsolidationReport(considered=7, added=6, skipped=0)
    facts = store.current(include_pending=True)
    assert len(facts) == 6
    assert {fact.source_ids for fact in facts} == {
        (event["id"],) for event in heard
    }

    by_source = {
        fact.source_ids[0]: fact for fact in store.current(include_pending=True)
    }
    expected = [
        (0, "name", "Олена", "Тебе звати Олена."),
        (1, "prefers", "каву", "Ти любиш каву."),
        (2, "prefers", "джаз", "Ти любиш джаз."),
        (3, "works_on", "heare", "Ти працюєш над heare."),
        (4, "lives_in", "Києві", "Ти живеш у Києві."),
        (5, "commitment", "зателефонувати мамі",
         "Не забути: зателефонувати мамі."),
    ]
    for index, predicate, value, text in expected:
        fact = by_source[heard[index]["id"]]
        assert (fact.predicate, fact.value, fact.text) == (predicate, value, text)

    for fact in facts:
        assert fact.subject == "owner"
        assert fact.source == CONSOLIDATED
        assert fact.trust == USER_SAID
        assert fact.status == PENDING
        assert fact.confidence == 0.8
        assert fact.valid_from == 900.0
        assert fact.created_at == 1000.0


def test_pending_facts_are_hidden_until_confirmed(tmp_path):
    store, _, _, _, _ = _adding_pass(tmp_path)

    assert store.current() == []
    assert len(store.current(include_pending=True)) == 6


def test_a_second_pass_adds_nothing_and_skips_known_sources(tmp_path):
    store, journal, _, heard, _ = _adding_pass(tmp_path)

    again = consolidate(store, journal, now=2000.0)

    assert again == ConsolidationReport(considered=7, added=0, skipped=6)
    assert len(store.current(include_pending=True)) == 6
    assert all(store.has_source(event["id"]) for event in heard)


def test_non_matching_and_said_events_are_ignored(tmp_path):
    journal = Journal(tmp_path / "events")
    _heard(journal, "Сьогодні гарна погода.")
    journal.append(
        "said",
        {"text": "Мене звати Олена.", "channel": "voice"},
        source="engine",
        trust="system",
    )
    store = FakeFacts()

    report = consolidate(store, journal, now=1000.0)

    assert report == ConsolidationReport(considered=1, added=0, skipped=0)
    assert store.current(include_pending=True) == []


def test_the_same_phrase_on_two_days_becomes_one_fact(tmp_path):
    journal = Journal(tmp_path / "events")
    _heard(journal, "Я люблю каву.", at=900.0)
    _heard(journal, "Я люблю каву.", at=900.0 + 86400)
    store = FakeFacts()

    report = consolidate(store, journal, now=1000.0)

    assert report == ConsolidationReport(considered=2, added=1, skipped=1)
    assert len(store.current(include_pending=True)) == 1

    again = consolidate(store, journal, now=2000.0)

    assert again.added == 0
    assert len(store.current(include_pending=True)) == 1


def test_the_cursor_considers_only_new_heard_lines(tmp_path):
    journal = Journal(tmp_path / "events")
    state_path = tmp_path / "memory" / "consolidate.json"
    _heard(journal, "Я люблю каву.")
    store = FakeFacts()

    first = consolidate(store, journal, now=1000.0, state_path=state_path)

    assert first == ConsolidationReport(considered=1, added=1, skipped=0)

    again = consolidate(store, journal, now=2000.0, state_path=state_path)

    assert again == ConsolidationReport(considered=0, added=0, skipped=0)

    _heard(journal, "Я живу в Києві.")
    third = consolidate(store, journal, now=3000.0, state_path=state_path)

    assert third == ConsolidationReport(considered=1, added=1, skipped=0)
    facts = store.current(include_pending=True)
    assert {fact.predicate for fact in facts} == {"prefers", "lives_in"}

