"""Forgetting leaves the journal and recall together."""

from __future__ import annotations

import asyncio

from heare.memory import search
from heare.memory.book import Book
from heare.memory.cli import main
from heare.memory.journal import Journal

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
