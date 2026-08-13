"""Tests for src/spine/persist.py against a temp SQLite db.

Never touches ~/.heare — every test opens SpinePersistence (and, where
relevant, storage.py's TranscriptStore) against a tmp_path DB.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.spine.persist import SpinePersistence
from src.store.storage import TranscriptStore


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "heare.db"


@pytest.fixture
def persist(db_path: Path):
    p = SpinePersistence(db_path)
    try:
        yield p
    finally:
        p.close()


def _row(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> sqlite3.Row:
    conn.row_factory = sqlite3.Row
    cur = conn.execute(sql, params)
    row = cur.fetchone()
    assert row is not None
    return row


def test_log_user_turn_creates_transcript_and_turn_row(
    persist: SpinePersistence, db_path: Path
) -> None:
    turn_id = persist.log_user_turn("hello there", fragment_count=3)
    assert turn_id > 0

    conn = sqlite3.connect(db_path)
    try:
        t_row = _row(
            conn,
            "SELECT text, mode, agent_spoken, turn_id FROM transcripts WHERE turn_id = ?",
            (turn_id,),
        )
        assert t_row["text"] == "hello there"
        assert t_row["mode"] == "spine"
        assert t_row["agent_spoken"] == 0
        assert t_row["turn_id"] == turn_id

        turn_row = _row(
            conn,
            "SELECT aggregated_text, utterance_count, start_ts, end_ts FROM turns WHERE id = ?",
            (turn_id,),
        )
        assert turn_row["aggregated_text"] == "hello there"
        assert turn_row["utterance_count"] == 3
        assert turn_row["start_ts"] is not None
        assert turn_row["end_ts"] is not None
    finally:
        conn.close()


def test_log_agent_reply_sets_agent_spoken_and_end_ts(
    persist: SpinePersistence, db_path: Path
) -> None:
    turn_id = persist.log_user_turn("what time is it")

    conn = sqlite3.connect(db_path)
    try:
        before = _row(conn, "SELECT end_ts FROM turns WHERE id = ?", (turn_id,))
        before_end_ts = before["end_ts"]
    finally:
        conn.close()

    persist.log_agent_reply("it is noon", turn_id)

    conn = sqlite3.connect(db_path)
    try:
        reply_row = _row(
            conn,
            "SELECT text, mode, agent_spoken, turn_id FROM transcripts WHERE agent_spoken = 1",
        )
        assert reply_row["text"] == "it is noon"
        assert reply_row["mode"] == "spine"
        assert reply_row["turn_id"] == turn_id

        turn_row = _row(conn, "SELECT end_ts FROM turns WHERE id = ?", (turn_id,))
        assert turn_row["end_ts"] >= before_end_ts
    finally:
        conn.close()


def test_recent_exchanges_order_and_limit(persist: SpinePersistence) -> None:
    t1 = persist.log_user_turn("one")
    persist.log_agent_reply("uno", t1)
    t2 = persist.log_user_turn("two")
    persist.log_agent_reply("dos", t2)
    t3 = persist.log_user_turn("three")
    persist.log_agent_reply("tres", t3)

    all_exchanges = persist.recent_exchanges(n=10)
    assert all_exchanges == [
        {"user": "one", "agent": "uno"},
        {"user": "two", "agent": "dos"},
        {"user": "three", "agent": "tres"},
    ]

    limited = persist.recent_exchanges(n=2)
    assert limited == [
        {"user": "two", "agent": "dos"},
        {"user": "three", "agent": "tres"},
    ]


def test_recent_exchanges_tolerates_turn_with_no_reply(
    persist: SpinePersistence,
) -> None:
    t1 = persist.log_user_turn("answered")
    persist.log_agent_reply("yep", t1)
    persist.log_user_turn("unanswered yet")

    exchanges = persist.recent_exchanges(n=10)
    assert exchanges == [
        {"user": "answered", "agent": "yep"},
        {"user": "unanswered yet", "agent": None},
    ]


def test_two_sequential_turns_get_distinct_ids_and_pairing(
    persist: SpinePersistence,
) -> None:
    t1 = persist.log_user_turn("first turn")
    persist.log_agent_reply("first reply", t1)
    t2 = persist.log_user_turn("second turn")
    persist.log_agent_reply("second reply", t2)

    assert t1 != t2

    exchanges = persist.recent_exchanges(n=10)
    assert exchanges == [
        {"user": "first turn", "agent": "first reply"},
        {"user": "second turn", "agent": "second reply"},
    ]


async def test_works_against_db_pre_created_by_storage_py(db_path: Path) -> None:
    # storage.py's own TranscriptStore inits the DB first (as the daemon
    # would on a fresh install) — SpinePersistence must be able to open
    # that same file afterwards without touching its schema.
    store = TranscriptStore(db_path)
    await store.init()
    await store.close()

    persist = SpinePersistence(db_path)
    try:
        turn_id = persist.log_user_turn("hello from a daemon-created db")
        persist.log_agent_reply("hi back", turn_id)
        exchanges = persist.recent_exchanges(n=5)
        assert exchanges == [
            {"user": "hello from a daemon-created db", "agent": "hi back"}
        ]
    finally:
        persist.close()


def test_unicode_ukrainian_round_trips(persist: SpinePersistence) -> None:
    user_text = "Привіт, як справи?"
    agent_text = "Все добре, дякую!"
    turn_id = persist.log_user_turn(user_text, fragment_count=2)
    persist.log_agent_reply(agent_text, turn_id)

    exchanges = persist.recent_exchanges(n=1)
    assert exchanges == [{"user": user_text, "agent": agent_text}]
