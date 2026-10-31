"""The quality signals a turn learns about itself reach the journal."""

from __future__ import annotations

from heare.memory.book import Book
from heare.memory.journal import Journal

from scripts.voice_latency import _turns


def _book(tmp_path) -> tuple[Book, Journal]:
    journal = Journal(tmp_path / "events")
    book = Book(
        tmp_path / "heare.db",
        profile_dir=tmp_path / "memory",
        journal=journal,
    )
    return book, journal


async def test_a_finished_turn_keeps_its_timings(tmp_path):
    book, journal = _book(tmp_path)
    try:
        await book.keep("assistant", "готово", "voice", {
            "failed": None, "seconds": 1.25, "first": 0.4,
            "calls": 2, "looked": 1,
        })

        said = next(row for row in journal.events() if row["kind"] == "said")
        payload = said["payload"]
        assert payload["text"] == "готово"
        assert payload["channel"] == "voice"
        assert payload["failed"] is None
        assert payload["seconds"] == 1.25
        assert payload["first"] == 0.4
        assert payload["calls"] == 2
        assert payload["looked"] == 1
    finally:
        book.close()


async def test_latency_reader_sees_a_turn_the_book_wrote(tmp_path):
    book, _journal = _book(tmp_path)
    try:
        await book.keep("assistant", "готово", "voice", {
            "failed": None, "seconds": 1.25, "first": 0.4,
            "calls": 2, "looked": 1,
        })

        turns = _turns(tmp_path / "events", 1)
        assert len(turns) == 1
        assert turns[0].seconds == 1.25
        assert turns[0].calls == 2
        assert turns[0].looked == 1
    finally:
        book.close()
