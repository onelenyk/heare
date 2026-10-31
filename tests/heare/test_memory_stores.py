"""One turn has a single write path and reaches both stores.

``Book.keep`` writes SQLite and the journal; ``Journal.run`` only mirrors
system events, so it must never write the same turn again. SQLite remains
a second store until Phase 2.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time

import pytest

from heare.core import Core, Message
from heare.language import uk
from heare.memory.book import Book
from heare.memory.journal import Journal
from heare.memory.persist import SpinePersistence
from tests.heare.fakes import Line, Page, Script


async def test_one_turn_reaches_both_stores(tmp_path):
    journal = Journal(tmp_path / "events")
    book = Book(
        tmp_path / "heare.db", profile_dir=tmp_path / "memory", journal=journal
    )
    model, line, page = Script(), Line(), Page()
    core = Core(model, memory=book, names=frozenset({"дока"}), words=uk)
    core.add_input(line)
    core.add_output(page)
    model.will_say("вітаю")

    running = [
        asyncio.create_task(core.run()),
        asyncio.create_task(journal.run(core.state)),
    ]
    try:
        await asyncio.sleep(0)
        core.hear(Message(text="привіт", channel="text"))
        for _ in range(200):
            if len(journal.tail_messages()) == 2:
                break
            await asyncio.sleep(0.01)

        tail = journal.tail_messages()
        assert [(m["role"], m["content"].strip()) for m in tail] == [
            ("user", "привіт"),
            ("assistant", "вітаю"),
        ]
        exchanges = await book.recent(5)
        assert len(exchanges) == 1
        assert exchanges[0]["user"].strip() == "привіт"
        assert exchanges[0]["agent"].strip() == "вітаю"
    finally:
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        book.close()


async def test_book_keep_journal_then_db_crash_does_not_hide_the_turn(
    tmp_path, monkeypatch
):
    journal = Journal(tmp_path / "events")
    book = Book(
        tmp_path / "heare.db", profile_dir=tmp_path / "memory", journal=journal
    )
    turn = "Цей хід не має загубитися."
    try:
        await book.keep("user", "Перший хід.", "text")
        await book.keep("assistant", "Добре.", "text")

        def crash(*_args, **_kwargs) -> int:
            raise RuntimeError("killed between the journal and SQLite")

        monkeypatch.setattr(book._store, "log_user_turn", crash)
        with pytest.raises(RuntimeError):
            await book.keep("user", turn, "text")
        assert any(
            event["payload"].get("text") == turn for event in journal.events()
        )
    finally:
        book.close()

    reopened = Book(
        tmp_path / "heare.db", profile_dir=tmp_path / "memory", journal=journal
    )
    try:
        exchanges = await reopened.recent(10)
        users = [row["user"] for row in exchanges if row["user"]]
        assert users.count(turn) == 1
        assert [m["content"] for m in journal.tail_messages()].count(turn) == 1
    finally:
        reopened.close()


async def test_a_forgotten_turn_is_not_replayed_by_the_reconcile(
    tmp_path, monkeypatch
):
    journal = Journal(tmp_path / "events")
    book = Book(
        tmp_path / "heare.db", profile_dir=tmp_path / "memory", journal=journal
    )
    forgotten = "Це забуте ще до запису в базу."
    try:
        def crash(*_args, **_kwargs) -> int:
            raise RuntimeError("killed between the journal and SQLite")

        monkeypatch.setattr(book._store, "log_user_turn", crash)
        with pytest.raises(RuntimeError):
            await book.keep("user", forgotten, "text")
        heard = next(
            event for event in journal.events() if event.get("kind") == "heard"
        )
        await book.forget([heard["id"]])
    finally:
        book.close()

    reopened = Book(
        tmp_path / "heare.db", profile_dir=tmp_path / "memory", journal=journal
    )
    try:
        assert journal.tail_messages() == []
        assert all(
            forgotten not in (row["user"] or "")
            for row in await reopened.recent(10)
        )
    finally:
        reopened.close()


def _a_turn_from_the_previous_release(
    db_path, journal: Journal, text: str
) -> None:
    """v0.3.0 wrote the row and the event but knew no journal_applied:
    the two stores hold the turn without a marker linking them."""
    store = SpinePersistence(db_path)
    try:
        store.log_user_turn(text)
    finally:
        store.close()
    journal.append(
        "heard",
        {"text": text, "channel": "typed"},
        source="typed",
        trust="user_said",
    )


async def test_forget_after_an_upgrade_does_not_duplicate_old_turns(tmp_path):
    journal = Journal(tmp_path / "events")
    old = "Старий хід до оновлення."
    _a_turn_from_the_previous_release(tmp_path / "heare.db", journal, old)

    book = Book(
        tmp_path / "heare.db", profile_dir=tmp_path / "memory", journal=journal
    )
    try:
        await book.keep("user", "Забудь цей хід.", "text")
        await book.keep("assistant", "Добре.", "text")
        heard = next(
            event
            for event in journal.events()
            if event.get("payload", {}).get("text") == "Забудь цей хід."
        )
        await book.forget([heard["id"]])
        assert book._store.journal_baseline() == (journal.end()[0], 0)
    finally:
        book.close()

    reopened = Book(
        tmp_path / "heare.db", profile_dir=tmp_path / "memory", journal=journal
    )
    try:
        users = [
            row["user"] for row in await reopened.recent(20) if row["user"]
        ]
        assert users.count(old) == 1
        assert reopened._store.journal_baseline() == (journal.end()[0], 0)
    finally:
        reopened.close()


async def test_a_second_restart_after_forget_does_not_rescan_all_history(
    tmp_path,
):
    journal = Journal(tmp_path / "events")
    book = Book(
        tmp_path / "heare.db", profile_dir=tmp_path / "memory", journal=journal
    )
    try:
        await book.keep("user", "Перший хід.", "text")
        await book.keep("assistant", "Другий хід.", "text")
        heard = next(
            event for event in journal.events() if event.get("kind") == "heard"
        )
        await book.forget([heard["id"]])
        marker = book._store.journal_baseline()
    finally:
        book.close()

    assert marker is not None
    assert marker != (None, 0)
    # The anchor is the start of the newest day file, not its end: the
    # next start re-reads it and journal_applied keeps that a no-op.
    assert marker == (journal.end()[0], 0)

    reopened = Book(
        tmp_path / "heare.db", profile_dir=tmp_path / "memory", journal=journal
    )
    try:
        assert reopened._store.journal_baseline() == marker
        seen = [
            text
            for row in await reopened.recent(10)
            for text in (row["user"], row["agent"])
            if text
        ]
        assert seen.count("Другий хід.") == 1
    finally:
        reopened.close()

    again = Book(
        tmp_path / "heare.db", profile_dir=tmp_path / "memory", journal=journal
    )
    try:
        assert again._store.journal_baseline() == marker
    finally:
        again.close()


async def test_forget_after_a_failed_reconcile_does_not_skip_unapplied_events(
    tmp_path, monkeypatch
):
    journal = Journal(tmp_path / "events")
    book = Book(
        tmp_path / "heare.db", profile_dir=tmp_path / "memory", journal=journal
    )
    poison = "Це зламає повтор."
    survivor = "Цей хід має пережити забуття."
    try:
        await book.keep("user", "Забудь цей хід.", "text")
        forgotten = next(
            event
            for event in journal.events()
            if event.get("payload", {}).get("text") == "Забудь цей хід."
        )
        journal.append(
            "heard",
            {"text": poison, "channel": "typed"},
            source="typed",
            trust="user_said",
        )
        journal.append(
            "heard",
            {"text": survivor, "channel": "typed"},
            source="typed",
            trust="user_said",
        )

        real = book._store.log_user_turn

        def raise_on_poison(text, *args, **kwargs):
            if text == poison:
                raise RuntimeError("SQLite is out of step")
            return real(text, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(book._store, "log_user_turn", raise_on_poison)
            await book.forget([forgotten["id"]])

        assert book._store.journal_baseline() == (None, 0)
    finally:
        book.close()

    reopened = Book(
        tmp_path / "heare.db", profile_dir=tmp_path / "memory", journal=journal
    )
    try:
        users = [
            row["user"] for row in await reopened.recent(20) if row["user"]
        ]
        assert users.count(survivor) == 1
    finally:
        reopened.close()


async def test_a_turn_appended_during_forget_is_not_lost(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "events")
    book = Book(
        tmp_path / "heare.db", profile_dir=tmp_path / "memory", journal=journal
    )
    other = Book(
        tmp_path / "heare.db",
        profile_dir=tmp_path / "memory",
        journal=Journal(tmp_path / "events"),
    )
    started = threading.Event()
    release = threading.Event()
    real_compact = journal.compact

    def paused_compact(ids):
        started.set()
        assert release.wait(timeout=10)
        return real_compact(ids)

    raced = "Цей хід прийшов під час забуття."
    try:
        await book.keep("user", "Забудь цей хід.", "text")
        forgotten = next(
            event
            for event in journal.events()
            if event.get("payload", {}).get("text") == "Забудь цей хід."
        )

        def crash(*_args, **_kwargs) -> int:
            raise RuntimeError("the other process died before its insert")

        monkeypatch.setattr(other._store, "log_user_turn", crash)
        monkeypatch.setattr(journal, "compact", paused_compact)

        forgetting = asyncio.create_task(book.forget([forgotten["id"]]))
        try:
            assert await asyncio.to_thread(started.wait, 10)
            with pytest.raises(RuntimeError):
                await other.keep("user", raced, "text")
        finally:
            release.set()
        await forgetting
    finally:
        book.close()
        other.close()

    reopened = Book(
        tmp_path / "heare.db", profile_dir=tmp_path / "memory", journal=journal
    )
    try:
        users = [
            row["user"] for row in await reopened.recent(20) if row["user"]
        ]
        assert users.count(raced) == 1
    finally:
        reopened.close()


async def test_book_keep_survives_a_locked_database(tmp_path):
    book = Book(
        tmp_path / "heare.db", profile_dir=tmp_path / "memory"
    )
    blocker = sqlite3.connect(tmp_path / "heare.db")
    try:
        blocker.execute("PRAGMA busy_timeout=0")
        blocker.execute("BEGIN IMMEDIATE")
        keeping = asyncio.create_task(
            book.keep("user", "Запис під замком.", "text")
        )
        await asyncio.sleep(0.2)
        blocker.rollback()
        await asyncio.wait_for(keeping, timeout=5)

        exchanges = await book.recent(5)
        assert exchanges[0]["user"] == "Запис під замком."
    finally:
        blocker.close()
        book.close()


async def test_always_does_not_block_the_loop(tmp_path):
    class SlowFacts:
        def current(self, **_kwargs):
            time.sleep(0.25)
            return []

    book = Book(
        tmp_path / "heare.db",
        profile_dir=tmp_path / "memory",
        facts=SlowFacts(),
    )
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    ticker = asyncio.create_task(tick())
    try:
        assert await book.always() == ""
    finally:
        ticker.cancel()
        await asyncio.gather(ticker, return_exceptions=True)
        book.close()

    # A blocking call would leave the loop frozen for the whole 0.25 s.
    assert ticks >= 10


def test_search_without_an_index_or_with_a_bad_match_returns_empty_not_raises(
    tmp_path,
):
    store = SpinePersistence(tmp_path / "heare.db")
    try:
        store.log_user_turn("The deployment finished early.")
        store.fts_ready = False
        assert store.search_transcripts('"deployment"') == []
        store.fts_ready = True
        assert store.search_transcripts('"unterminated') == []
        assert [text for _, text, _ in store.search_transcripts('"deployment"')] == [
            "The deployment finished early."
        ]
    finally:
        store.close()


def test_a_broken_fts_index_is_rebuilt_once(tmp_path):
    path = tmp_path / "heare.db"
    store = SpinePersistence(path)
    try:
        store.log_user_turn("The deployment finished early.")
        assert store.search_health()
    finally:
        store.close()

    broken = sqlite3.connect(path)
    try:
        broken.executescript(
            "DROP TRIGGER IF EXISTS transcripts_ai;"
            "DROP TRIGGER IF EXISTS transcripts_ad;"
            "DROP TRIGGER IF EXISTS transcripts_au;"
            "DROP TABLE IF EXISTS transcripts_fts;"
        )
        broken.commit()
    finally:
        broken.close()

    repaired = SpinePersistence(path)
    try:
        assert repaired.fts_rebuilds == 1
        assert repaired.search_health()
        assert [
            text for _, text, _ in repaired.search_transcripts('"deployment"')
        ] == ["The deployment finished early."]
        assert repaired.search_transcripts('"unterminated') == []
        assert repaired.fts_rebuilds == 1
    finally:
        repaired.close()

    healthy = SpinePersistence(path)
    try:
        assert healthy.fts_rebuilds == 0
        assert healthy.search_health()
    finally:
        healthy.close()


async def test_an_assistant_only_message_does_not_extend_a_previous_turn(tmp_path):
    book = Book(tmp_path / "heare.db", profile_dir=tmp_path / "memory")
    try:
        await book.keep("user", "привіт", "text")
        await book.keep("assistant", "вітаю", "text")
        await book.keep("assistant", "Нагадування: пити воду", "engine")

        exchanges = await book.recent(5)
        assert len(exchanges) == 2
        assert exchanges[0]["user"].strip() == "привіт"
        assert exchanges[0]["agent"].strip() == "вітаю"
        assert exchanges[1]["user"] is None
        assert exchanges[1]["agent"].strip() == "Нагадування: пити воду"

        await book.keep("user", "ще", "text")
        await book.keep("assistant", "звісно", "text")

        exchanges = await book.recent(5)
        assert len(exchanges) == 3
        assert exchanges[2]["user"].strip() == "ще"
        assert exchanges[2]["agent"].strip() == "звісно"
    finally:
        book.close()
