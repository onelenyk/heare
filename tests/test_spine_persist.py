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


# -- role sessions --------------------------------------------------------


def test_role_session_round_trip_open_note_close(
    persist: SpinePersistence, db_path: Path
) -> None:
    t1 = persist.log_user_turn("Почнімо нараду")
    persist.log_agent_reply("Добре", t1)
    t2 = persist.log_user_turn("Рішення номер один")

    session_id = persist.open_role_session("мітинг", "log")
    assert session_id > 0

    live = persist.live_role_session()
    assert live is not None
    assert live["id"] == session_id
    assert live["role_name"] == "мітинг"
    assert live["channel"] == "log"
    assert live["status"] == "live"
    assert live["ended_ts"] is None
    assert live["turn_ids"] == []

    persist.note_role_turn(session_id, t1)
    persist.note_role_turn(session_id, None)  # ignored
    persist.note_role_turn(session_id, t2)
    persist.note_role_turn(session_id, t2)  # a retry must not double-count

    still_live = persist.live_role_session()
    assert still_live is not None
    assert still_live["turn_ids"] == [t1, t2]

    persist.close_role_session(session_id, "/tmp/artifact.md")

    # closed: no longer live
    assert persist.live_role_session() is None

    conn = sqlite3.connect(db_path)
    try:
        row = _row(
            conn,
            "SELECT ended_ts, artifact_path, status, turn_ids "
            "FROM role_sessions WHERE id = ?",
            (session_id,),
        )
        assert row["ended_ts"] is not None
        assert row["artifact_path"] == "/tmp/artifact.md"
        assert row["status"] == "done"
        assert row["turn_ids"] == f"[{t1}, {t2}]"
    finally:
        conn.close()


def test_role_session_turns_returns_exchanges_in_session_order(
    persist: SpinePersistence,
) -> None:
    t1 = persist.log_user_turn("перше питання")
    persist.log_agent_reply("перша відповідь", t1)
    t2 = persist.log_user_turn("друге питання")  # no reply yet
    outside = persist.log_user_turn("не з наради")
    persist.log_agent_reply("теж не з наради", outside)

    session_id = persist.open_role_session("мітинг", "log")
    persist.note_role_turn(session_id, t1)
    persist.note_role_turn(session_id, t2)

    turns = persist.role_session_turns(session_id)
    assert turns == [
        {"turn_id": t1, "user": "перше питання", "agent": "перша відповідь"},
        {"turn_id": t2, "user": "друге питання", "agent": None},
    ]

    # unknown session id is empty, not an error
    assert persist.role_session_turns(9999) == []


def test_live_role_session_survives_reopen_and_is_recovered_once(
    db_path: Path,
) -> None:
    # A daemon that was killed mid-meeting: session opened, turns noted,
    # never closed.
    first = SpinePersistence(db_path)
    try:
        turn_id = first.log_user_turn("нарада триває")
        session_id = first.open_role_session("мітинг", "log")
        first.note_role_turn(session_id, turn_id)
    finally:
        first.close()

    # Restart: a brand-new process, same file.
    second = SpinePersistence(db_path)
    try:
        row = second.live_role_session()
        assert row is not None
        assert row["id"] == session_id
        assert row["role_name"] == "мітинг"
        assert row["turn_ids"] == [turn_id]
        assert row["status"] == "live"

        second.close_role_session(row["id"], None, "interrupted")

        # Reported exactly once: the next boot finds nothing.
        assert second.live_role_session() is None
        recovered = second.role_session_turns(session_id)
        assert recovered == [
            {"turn_id": turn_id, "user": "нарада триває", "agent": None}
        ]
    finally:
        second.close()


async def test_startup_recovery_marks_interrupted_and_tells_the_dashboard(
    db_path: Path,
) -> None:
    import json

    from src.daemon.spine_engine import _recover_role_session

    class FakeState:
        def __init__(self) -> None:
            self.cache: dict[str, str] = {}

        def set_cache_only(self, key: str, value: str) -> None:
            self.cache[key] = value

    p = SpinePersistence(db_path)
    state = FakeState()
    try:
        # nothing live -> nothing said
        assert await _recover_role_session(p, state) is None
        assert state.cache == {}

        t1 = p.log_user_turn("перше")
        t2 = p.log_user_turn("друге")
        session_id = p.open_role_session("мітинг", "log")
        p.note_role_turn(session_id, t1)
        p.note_role_turn(session_id, t2)

        row = await _recover_role_session(p, state)
        assert row is not None
        assert row["id"] == session_id

        payload = json.loads(state.cache["role_interrupted"])
        assert payload["role"] == "мітинг"
        assert payload["turns"] == 2
        assert payload["ts"] > 0

        # marked interrupted, and never reported a second time
        conn = sqlite3.connect(db_path)
        try:
            stored = _row(
                conn,
                "SELECT status, ended_ts FROM role_sessions WHERE id = ?",
                (session_id,),
            )
            assert stored["status"] == "interrupted"
            assert stored["ended_ts"] is not None
        finally:
            conn.close()

        state.cache.clear()
        assert await _recover_role_session(p, state) is None
        assert state.cache == {}

        # the turns are still there for a later artifact
        assert [t["user"] for t in p.role_session_turns(session_id)] == [
            "перше",
            "друге",
        ]
    finally:
        p.close()


def test_role_sessions_table_does_not_bump_shared_schema_version(
    persist: SpinePersistence, db_path: Path
) -> None:
    from src.store.storage import SCHEMA_VERSION

    conn = sqlite3.connect(db_path)
    try:
        row = _row(conn, "SELECT value FROM meta WHERE key = 'schema_version'")
        assert int(row["value"]) == SCHEMA_VERSION
    finally:
        conn.close()


async def test_role_sessions_added_to_a_storage_py_created_db(db_path: Path) -> None:
    # The daemon's own store creates the file first; the spine's extra
    # table must be additive and leave that DB openable by storage.py.
    store = TranscriptStore(db_path)
    await store.init()
    await store.close()

    p = SpinePersistence(db_path)
    try:
        session_id = p.open_role_session("мітинг", "voice")
        p.close_role_session(session_id, None, "done")
    finally:
        p.close()

    store = TranscriptStore(db_path)
    await store.init()
    await store.close()


def test_unicode_ukrainian_round_trips(persist: SpinePersistence) -> None:
    user_text = "Привіт, як справи?"
    agent_text = "Все добре, дякую!"
    turn_id = persist.log_user_turn(user_text, fragment_count=2)
    persist.log_agent_reply(agent_text, turn_id)

    exchanges = persist.recent_exchanges(n=1)
    assert exchanges == [{"user": user_text, "agent": agent_text}]
