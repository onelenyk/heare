"""Confirmed facts reach the prompt; proposed ones wait for review."""

from __future__ import annotations

from datetime import datetime

from heare.memory.book import Book
from heare.memory.contracts import CONFIRMED, PENDING, Fact
from heare.memory.facts import SQLiteFacts
from heare.memory.profile import Profile


async def test_the_book_quotes_confirmed_facts_and_not_pending_ones(tmp_path):
    db = tmp_path / "heare.db"
    facts = SQLiteFacts(db)
    facts.add(
        Fact(
            subject="owner",
            predicate="name",
            value="Назар",
            text="Тебе звати Назар.",
            source="consolidated",
            trust="user_said",
            status=CONFIRMED,
            valid_from=0.0,
            source_ids=("c",),
            created_at=1.0,
        )
    )
    facts.add(
        Fact(
            subject="owner",
            predicate="prefers",
            value="каву",
            text="Ти любиш каву.",
            source="consolidated",
            trust="user_said",
            status=PENDING,
            valid_from=0.0,
            source_ids=("p",),
            created_at=1.0,
        )
    )
    Profile(tmp_path / "memory").write("user", "Люблю короткі відповіді.")

    book = Book(db, profile_dir=tmp_path / "memory")
    try:
        text = await book.always()
    finally:
        book.close()

    assert "Люблю короткі відповіді." in text
    assert "Тебе звати Назар." in text
    assert "Ти любиш каву." not in text


async def test_a_fact_carries_the_day_it_was_learned_and_an_old_one_has_none(
    tmp_path,
):
    db = tmp_path / "heare.db"
    facts = SQLiteFacts(db)
    facts.add(
        Fact(
            subject="owner",
            predicate="lives_in",
            value="Kyiv",
            text="You live in Kyiv.",
            source="consolidated",
            trust="user_said",
            status=CONFIRMED,
            valid_from=1700000000.0,
            source_ids=("dated",),
            created_at=1700000000.0,
        )
    )
    facts.add(
        Fact(
            subject="owner",
            predicate="likes",
            value="tea",
            text="You like tea.",
            source="consolidated",
            trust="user_said",
            status=CONFIRMED,
            valid_from=0.0,
            source_ids=("undated",),
            created_at=1700000000.0,
        )
    )

    book = Book(db, profile_dir=tmp_path / "memory")
    try:
        text = await book.always()
    finally:
        book.close()

    day = datetime.fromtimestamp(1700000000.0).strftime("%Y-%m-%d")
    lines = text.splitlines()
    assert f"- {day}: You live in Kyiv." in lines
    assert "- You like tea." in lines
    assert "1970" not in text
