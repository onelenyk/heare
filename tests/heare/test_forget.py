"""Forgetting leaves the journal and recall together."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import date

from heare.memory import search
from heare.memory.book import Book
from heare.memory.cli import main
from heare.memory.consolidate import consolidate
from heare.memory.facts import SQLiteFacts
from heare.memory.journal import Journal
from heare.memory.review import notice, review_day
from tests.heare.fakes import Script

UNIQUE = "унікальниймаркер"


def _book(tmp_path) -> tuple[Book, Journal]:
    journal = Journal(tmp_path / "events")
    book = Book(
        tmp_path / "heare.db",
        profile_dir=tmp_path / "memory",
        journal=journal,
    )
    return book, journal


async def _one_turn(book: Book, journal: Journal) -> list[str]:
    await book.keep("user", f"Мій {UNIQUE} — синій.", "text")
    await book.keep("assistant", "Запам'ятав.", "text")
    return [
        event["id"]
        for event in journal.events()
        if event.get("kind") in ("heard", "said")
    ]


async def test_forget_removes_the_words_from_the_journal_and_recall(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(search, "NOT_YET_A_MEMORY_S", 0.0)
    book, journal = _book(tmp_path)
    try:
        ids = await _one_turn(book, journal)
        assert UNIQUE in await book.recall(UNIQUE)

        assert await book.forget(ids) == 2

        assert journal.tail_messages() == []
        assert UNIQUE not in await book.recall(UNIQUE)
        assert all(UNIQUE not in str(item) for item in await book.recent(5))

        assert await book.forget(ids) == 0
        assert journal.tail_messages() == []
    finally:
        book.close()


def _seed(tmp_path) -> list[str]:
    async def seed() -> list[str]:
        book, journal = _book(tmp_path)
        try:
            return await _one_turn(book, journal)
        finally:
            book.close()

    return asyncio.run(seed())


def test_the_cli_forgets_ids_in_the_journal_and_in_recall(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(search, "NOT_YET_A_MEMORY_S", 0.0)
    ids = _seed(tmp_path)

    code = main([
        "forget", ids[0],
        "--db", str(tmp_path / "heare.db"),
        "--events", str(tmp_path / "events"),
    ])

    assert code == 0
    assert "Забув 1." in capsys.readouterr().out
    assert Journal(tmp_path / "events").tail_messages() == [
        {"role": "assistant", "content": "Запам'ятав."},
    ]

    async def recall() -> str:
        book = Book(
            tmp_path / "heare.db",
            profile_dir=tmp_path / "memory",
            journal=Journal(tmp_path / "events"),
        )
        try:
            return await book.recall(UNIQUE)
        finally:
            book.close()

    assert UNIQUE not in asyncio.run(recall())


def _review(answer: list[dict]) -> Script:
    model = Script()
    model.will_say(json.dumps(answer))
    return model


async def test_forget_removes_derived_facts_from_the_prompt(tmp_path):
    journal = Journal(tmp_path / "events")
    facts = SQLiteFacts(tmp_path / "heare.db")
    book = Book(
        tmp_path / "heare.db",
        profile_dir=tmp_path / "memory",
        journal=journal,
        facts=facts,
    )
    try:
        await book.keep("user", "Мій улюблений напій — кава.", "text")
        await book.keep("assistant", "Ти любиш каву.", "text")
        heard = next(
            event for event in journal.events() if event.get("kind") == "heard"
        )
        report = await notice(
            facts,
            journal,
            _review([{
                "predicate": "favourite_drink",
                "value": "кава",
                "text": "Your favourite drink is coffee.",
                "confidence": 0.9,
            }]),
            state_path=tmp_path / "memory" / "notice.json",
            now=time.time(),
        )
        assert report.added == 1
        assert "Your favourite drink is coffee." in await book.always()

        await book.forget([heard["id"]])

        assert "coffee" not in (await book.always()).casefold()
        assert facts.current() == []
    finally:
        book.close()


async def test_forget_stops_the_paid_passes_relearning(tmp_path):
    journal = Journal(tmp_path / "events")
    facts = SQLiteFacts(tmp_path / "heare.db")
    book = Book(
        tmp_path / "heare.db",
        profile_dir=tmp_path / "memory",
        journal=journal,
        facts=facts,
    )
    state_path = tmp_path / "memory" / "consolidate.json"
    try:
        await book.keep("user", "Я люблю каву.", "text")
        heard = next(
            event for event in journal.events() if event.get("kind") == "heard"
        )
        assert consolidate(facts, journal, state_path=state_path).added == 1

        await book.forget([heard["id"]])

        assert facts.current(include_pending=True) == []
        assert consolidate(facts, journal, state_path=state_path).added == 0
        assert consolidate(
            facts, journal, state_path=state_path
        ).considered == 0

        model = _review([{
            "predicate": "prefers",
            "value": "каву",
            "text": "You like coffee.",
            "confidence": 0.9,
        }])
        report = await notice(
            facts,
            journal,
            model,
            state_path=tmp_path / "memory" / "notice.json",
            now=time.time(),
        )
        assert report.added == 0
        assert model.asked == []

        daily = _review([{
            "predicate": "prefers",
            "value": "каву",
            "text": "You like coffee.",
            "confidence": 0.9,
        }])
        daily_report = await review_day(
            facts, journal, daily, day=date.today(), now=time.time()
        )
        assert daily_report.added == 0
        assert daily.asked == []
    finally:
        book.close()


async def test_forgetting_one_line_keeps_a_fact_learned_elsewhere(tmp_path):
    journal = Journal(tmp_path / "events")
    facts = SQLiteFacts(tmp_path / "heare.db")
    book = Book(
        tmp_path / "heare.db",
        profile_dir=tmp_path / "memory",
        journal=journal,
        facts=facts,
    )
    try:
        await book.keep("user", "Я люблю каву.", "text")
        await book.keep("user", "Я дуже люблю каву.", "text")
        heard = [
            event for event in journal.events() if event.get("kind") == "heard"
        ]
        consolidate(facts, journal)
        fact = facts.current(include_pending=True)[0]
        assert fact.id is not None
        facts.confirm(fact.id)
        assert "каву" in await book.always()

        await book.forget([heard[0]["id"]])

        assert [current.value for current in facts.current()] == ["каву"]
        assert "каву" in await book.always()
    finally:
        book.close()
