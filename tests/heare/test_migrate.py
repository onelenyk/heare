"""The old memories table becomes pending facts, once."""

from __future__ import annotations

import sqlite3

from heare.memory.contracts import LEGACY, PENDING, MigrationReport
from heare.memory.migrate import migrate
from tests.heare.fake_facts import FakeFacts

SCHEMA = """
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    type TEXT,
    content TEXT,
    source TEXT,
    confidence REAL,
    created_ts REAL,
    last_accessed REAL,
    access_count INTEGER,
    archived INTEGER,
    metadata TEXT
)
"""

ROWS = [
    ("fact-1", "fact", "Люблю каву.", "user", 1.0, 100.0, 0),
    ("decision-1", "decision", "Перейти на Rust.", "model", 0.8, 200.0, 0),
    ("archived-1", "fact", "Стара річ.", "user", 1.0, 50.0, 1),
]


def _legacy(tmp_path, rows=ROWS, schema=SCHEMA):
    path = tmp_path / "heare.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute(schema)
        if rows:
            conn.executemany(
                "INSERT INTO memories "
                "(id, type, content, source, confidence, created_ts, archived) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        conn.commit()
    finally:
        conn.close()
    return path


def _pending(store):
    return store.current(include_pending=True)


def test_active_rows_become_pending_legacy_facts(tmp_path):
    store = FakeFacts()
    report = migrate(_legacy(tmp_path), store)

    assert report == MigrationReport(
        read=3, imported=2, archived=1, duplicates=0, dry_run=False
    )
    facts = {fact.predicate: fact for fact in _pending(store)}
    assert set(facts) == {"legacy_fact", "legacy_decision"}
    for fact in facts.values():
        assert fact.subject == "owner"
        assert fact.source == LEGACY
        assert fact.trust == LEGACY
        assert fact.status == PENDING
        assert fact.valid_from == fact.created_at
    assert facts["legacy_fact"].source_ids == ("legacy:fact-1",)
    assert facts["legacy_fact"].value == facts["legacy_fact"].text
    assert facts["legacy_decision"].source_ids == ("legacy:decision-1",)


def test_a_second_run_imports_nothing(tmp_path):
    store = FakeFacts()
    db = _legacy(tmp_path)
    migrate(db, store)

    report = migrate(db, store)

    assert report.read == 3
    assert report.imported == 0
    assert report.archived == 1
    assert report.duplicates == 2
    assert len(_pending(store)) == 2


def test_archived_rows_are_included_on_request(tmp_path):
    store = FakeFacts()
    report = migrate(_legacy(tmp_path), store, include_archived=True)

    assert report.read == 3
    assert report.imported == 3
    assert report.archived == 0
    assert len(_pending(store)) == 3


def test_a_dry_run_counts_without_writing(tmp_path):
    store = FakeFacts()
    report = migrate(_legacy(tmp_path), store, dry_run=True)

    assert report == MigrationReport(
        read=3, imported=2, archived=1, duplicates=0, dry_run=True
    )
    assert _pending(store) == []


def test_no_memories_table_is_an_empty_report(tmp_path):
    db = _legacy(tmp_path, rows=[], schema="CREATE TABLE elsewhere (id TEXT)")

    report = migrate(db, FakeFacts())

    assert report == MigrationReport()
