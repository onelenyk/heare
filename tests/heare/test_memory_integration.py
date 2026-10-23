"""The production memory path: a turn, a fact, and a forget.

The stores are wired the way the brain wires them, but with a temporary
root: this is the path the running app takes, not a unit of one module.
"""

from __future__ import annotations

from heare.memory.book import Book
from heare.memory.consolidate import consolidate
from heare.memory.facts import SQLiteFacts
from heare.memory.journal import Journal


async def test_a_turn_becomes_a_confirmed_fact_and_can_be_forgotten(tmp_path):
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
        await book.keep("assistant", "Добре.", "text")

        ids = [
            event["id"]
            for event in journal.events()
            if event.get("kind") in ("heard", "said")
        ]
        assert len(ids) == 2

        report = consolidate(
            facts,
            journal,
            state_path=tmp_path / "memory" / "consolidate.json",
        )
        assert report.added == 1

        pending = [
            fact
            for fact in facts.current(include_pending=True)
            if fact.predicate == "prefers"
        ]
        assert len(pending) == 1
        fact_id = pending[0].id
        assert fact_id is not None
        assert facts.confirm(fact_id)

        text = await book.always()
        assert "каву" in text

        await book.forget(ids)
        assert journal.tail_messages() == []
        assert "каву" not in await book.recall("каву")
    finally:
        book.close()
