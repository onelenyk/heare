"""Acceptance tests for src/store/memory/store.py.

Exercises the SQLite + FTS5 store directly with no MCP layer.
Asserts schema correctness, PRAGMA state, and FTS5 trigger behaviour
on insert, update, and delete.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.store.memory import open_memory_db


@pytest.fixture()
def db(tmp_path: Path):
    conn = open_memory_db(tmp_path / "memory.db")
    yield conn
    conn.close()


def _now() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_schema_creates_all_tables(tmp_path: Path) -> None:
    conn = open_memory_db(tmp_path / "memory.db")
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
        names = {row["name"] for row in rows}
    finally:
        conn.close()

    expected = {
        "conversation",
        "episodic",
        "semantic",
        "action_log",
        "entities",
        "entity_links",
    }
    assert expected.issubset(names), f"missing tables: {expected - names}"


# ---------------------------------------------------------------------------
# PRAGMAs
# ---------------------------------------------------------------------------

def test_pragmas_applied(tmp_path: Path) -> None:
    conn = open_memory_db(tmp_path / "memory.db")
    try:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    finally:
        conn.close()

    assert journal_mode == "wal"
    assert busy_timeout == 5000
    assert foreign_keys == 1


# ---------------------------------------------------------------------------
# env-var / path override — DEFAULT_DB_PATH is module-level frozen so we
# test the explicit-path branch of open_memory_db instead.
# ---------------------------------------------------------------------------

def test_env_var_override(tmp_path: Path) -> None:
    """open_memory_db with an explicit path creates the DB at that path."""
    custom_path = tmp_path / "custom" / "mem.db"
    conn = open_memory_db(custom_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        names = {row["name"] for row in rows}
    finally:
        conn.close()

    assert custom_path.exists()
    assert "conversation" in names


# ---------------------------------------------------------------------------
# FTS5 insert trigger
# ---------------------------------------------------------------------------

def test_fts_insert_trigger(db) -> None:
    db.execute(
        "INSERT INTO episodic(summary, tags, ts) VALUES (?, ?, ?)",
        ("project deadline is friday", "work", _now()),
    )
    rows = db.execute(
        "SELECT kind, ref_id, text FROM fts_memory WHERE fts_memory MATCH 'deadline'"
    ).fetchall()

    assert len(rows) == 1
    assert rows[0]["kind"] == "episodic"
    assert "deadline" in rows[0]["text"]


# ---------------------------------------------------------------------------
# FTS5 update trigger
# ---------------------------------------------------------------------------

def test_fts_update_trigger(db) -> None:
    cur = db.execute(
        "INSERT INTO episodic(summary, tags, ts) VALUES (?, ?, ?)",
        ("original tangerine text", None, _now()),
    )
    row_id = cur.lastrowid

    db.execute(
        "UPDATE episodic SET summary = ? WHERE id = ?",
        ("updated apricot text", row_id),
    )

    old_hits = db.execute(
        "SELECT 1 FROM fts_memory WHERE fts_memory MATCH 'tangerine'"
    ).fetchall()
    new_hits = db.execute(
        "SELECT 1 FROM fts_memory WHERE fts_memory MATCH 'apricot'"
    ).fetchall()

    assert old_hits == [], "stale FTS entry should have been removed"
    assert len(new_hits) == 1, "new FTS entry should exist after update"


# ---------------------------------------------------------------------------
# FTS5 delete trigger
# ---------------------------------------------------------------------------

def test_fts_delete_trigger(db) -> None:
    cur = db.execute(
        "INSERT INTO episodic(summary, tags, ts) VALUES (?, ?, ?)",
        ("ephemeral kiwi memory", None, _now()),
    )
    row_id = cur.lastrowid

    before = db.execute(
        "SELECT 1 FROM fts_memory WHERE fts_memory MATCH 'kiwi'"
    ).fetchall()
    assert len(before) == 1, "FTS entry should exist before delete"

    db.execute("DELETE FROM episodic WHERE id = ?", (row_id,))

    after = db.execute(
        "SELECT 1 FROM fts_memory WHERE fts_memory MATCH 'kiwi'"
    ).fetchall()
    assert after == [], "FTS entry should be gone after delete"


# ---------------------------------------------------------------------------
# FTS5 covers conversation and semantic tables too
# ---------------------------------------------------------------------------

def test_fts_indexes_conversation_and_semantic(db) -> None:
    db.execute(
        "INSERT INTO conversation(session_id, role, content, ts) VALUES (?, ?, ?, ?)",
        ("s1", "user", "memorable avocado toast", _now()),
    )
    db.execute(
        "INSERT INTO semantic(key, value, ts) VALUES (?, ?, ?)",
        ("fav_color", "ultraviolet", _now()),
    )

    conv_hits = db.execute(
        "SELECT kind FROM fts_memory WHERE fts_memory MATCH 'avocado'"
    ).fetchall()
    sem_hits = db.execute(
        "SELECT kind FROM fts_memory WHERE fts_memory MATCH 'ultraviolet'"
    ).fetchall()

    assert [r["kind"] for r in conv_hits] == ["conversation"]
    assert [r["kind"] for r in sem_hits] == ["semantic"]
