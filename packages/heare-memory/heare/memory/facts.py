"""Typed facts, in the same SQLite file memory already uses.

``facts`` is the store behind ``contracts.FactStore``: one statement
about the owner with where it came from and when it was true. It owns
its tables — ``facts``, ``fact_sources`` and ``facts_fts`` — rather than
joining ``storage.py``'s schema, because a fact is memory's, not the
transcript store's, and adding it there would tie the shared schema
version to a feature the audio pipeline never reads.

``fact_sources`` is what makes ``add`` idempotent: the same event,
migrated row or consolidation pass names the source it came from, and a
source can only ever point at one fact. The table is the uniqueness
constraint; the check in ``add`` is only how we return the first fact's
id instead of raising.

Connections are short-lived and per method. These calls are synchronous
and infrequent, and a connection opened and closed around one statement
is easier to reason about than a shared handle and a lock. WAL keeps
readers from blocking the brain's other writer in the same file.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator

from heare.memory.contracts import CONFIRMED, REJECTED, Fact

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value TEXT NOT NULL,
    text TEXT NOT NULL,
    source TEXT NOT NULL,
    trust TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence REAL NOT NULL,
    valid_from REAL NOT NULL,
    valid_to REAL,
    source_ids TEXT NOT NULL DEFAULT '[]',
    supersedes INTEGER,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_facts_interval
    ON facts(status, valid_from, valid_to);
CREATE INDEX IF NOT EXISTS idx_facts_subject
    ON facts(subject, predicate);

-- One row per event, migration row or consolidation pass. The primary
-- key is the idempotency guarantee: a second add with the same source
-- finds it here before touching facts.
CREATE TABLE IF NOT EXISTS fact_sources (
    source_id TEXT PRIMARY KEY,
    fact_id INTEGER NOT NULL REFERENCES facts(id)
);

CREATE INDEX IF NOT EXISTS idx_fact_sources_fact
    ON fact_sources(fact_id);
"""

# External-content FTS5: the index stores the three searchable columns
# and reads the rest from facts. The triggers live in the database file,
# so every writer keeps the index current, including one that has never
# heard of this module. Kept out of SCHEMA so a SQLite built without
# FTS5 loses search, not the ability to open the store.
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    text,
    value,
    predicate,
    content='facts',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, text, value, predicate)
    VALUES (new.id, new.text, new.value, new.predicate);
END;

CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, text, value, predicate)
    VALUES ('delete', old.id, old.text, old.value, old.predicate);
END;

CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, text, value, predicate)
    VALUES ('delete', old.id, old.text, old.value, old.predicate);
    INSERT INTO facts_fts(rowid, text, value, predicate)
    VALUES (new.id, new.text, new.value, new.predicate);
END;
"""

_INSERT = """
INSERT INTO facts (
    subject, predicate, value, text, source, trust, status, confidence,
    valid_from, valid_to, source_ids, supersedes, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _as_fact(row: sqlite3.Row) -> Fact:
    try:
        source_ids = tuple(json.loads(row["source_ids"] or "[]"))
    except (TypeError, ValueError):
        source_ids = ()
    return Fact(
        subject=row["subject"],
        predicate=row["predicate"],
        value=row["value"],
        text=row["text"],
        source=row["source"],
        trust=row["trust"],
        status=row["status"],
        confidence=row["confidence"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        source_ids=source_ids,
        supersedes=row["supersedes"],
        created_at=row["created_at"],
        id=row["id"],
    )


def _match(query: str) -> str | None:
    """Quote each token so user text cannot become FTS5 syntax."""
    tokens = _TOKEN_RE.findall(query)
    if not tokens:
        return None
    return " OR ".join(f'"{token}"' for token in tokens)


class SQLiteFacts:
    """Facts over SQLite, with their sources and a full-text index."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = Path.home() / ".heare" / "memory" / "heare.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            had_fts = self._has_table(conn, "facts_fts")
            conn.executescript(SCHEMA)
            conn.commit()
            self.fts_ready = self._ensure_fts(conn, had_fts)
        # Not only when this instance created the file: Book's
        # SpinePersistence usually makes it first, and an older app may
        # have left it readable. Whatever wrote it, the memories are the
        # owner's alone.
        try:
            self.db_path.chmod(0o600)
        except OSError:
            # A file we cannot chmod is still one we can use; the
            # permission is a courtesy on a shared machine, not a
            # reason to refuse to remember.
            pass

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _has_table(conn: sqlite3.Connection, name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone()
        return row is not None

    def _ensure_fts(self, conn: sqlite3.Connection, had_fts: bool) -> bool:
        try:
            conn.executescript(FTS_SCHEMA)
            if not had_fts:
                # A facts table that predates its index: fill it now.
                conn.execute(
                    "INSERT INTO facts_fts(facts_fts) VALUES ('rebuild')"
                )
            conn.commit()
        except sqlite3.OperationalError:
            return False
        return True

    @staticmethod
    def _source_fact(
        conn: sqlite3.Connection, source_ids: tuple[str, ...]
    ) -> int | None:
        for source_id in source_ids:
            row = conn.execute(
                "SELECT fact_id FROM fact_sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            if row is not None:
                return int(row["fact_id"])
        return None

    @staticmethod
    def _insert(conn: sqlite3.Connection, fact: Fact) -> int:
        cursor = conn.execute(
            _INSERT,
            (
                fact.subject,
                fact.predicate,
                fact.value,
                fact.text,
                fact.source,
                fact.trust,
                fact.status,
                fact.confidence,
                fact.valid_from,
                fact.valid_to,
                json.dumps(list(fact.source_ids)),
                fact.supersedes,
                fact.created_at,
            ),
        )
        fact_id = int(cursor.lastrowid or 0)
        for source_id in dict.fromkeys(fact.source_ids):
            conn.execute(
                "INSERT INTO fact_sources (source_id, fact_id)"
                " VALUES (?, ?)",
                (source_id, fact_id),
            )
        return fact_id

    def add(self, fact: Fact) -> int:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._source_fact(conn, fact.source_ids)
                if existing is not None:
                    conn.rollback()
                    return existing
                fact_id = self._insert(conn, fact)
                conn.commit()
                return fact_id
            except sqlite3.IntegrityError:
                conn.rollback()
                existing = self._source_fact(conn, fact.source_ids)
                if existing is not None:
                    return existing
                raise
            except Exception:
                conn.rollback()
                raise

    def has_source(self, source_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM fact_sources WHERE source_id = ? LIMIT 1",
                (source_id,),
            ).fetchone()
        return row is not None

    def get(self, fact_id: int) -> Fact | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM facts WHERE id = ?", (fact_id,)
            ).fetchone()
        return _as_fact(row) if row is not None else None

    @staticmethod
    def _window(
        now: float, include_pending: bool
    ) -> tuple[list[str], list[object]]:
        clauses = ["valid_from <= ?", "(valid_to IS NULL OR ? < valid_to)"]
        params: list[object] = [now, now]
        if not include_pending:
            clauses.append("status = ?")
            params.append(CONFIRMED)
        return clauses, params

    def current(
        self,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        now: float | None = None,
        include_pending: bool = False,
    ) -> list[Fact]:
        now = time.time() if now is None else now
        clauses, params = self._window(now, include_pending)
        if subject is not None:
            clauses.append("subject = ?")
            params.append(subject)
        if predicate is not None:
            clauses.append("predicate = ?")
            params.append(predicate)
        sql = (
            "SELECT * FROM facts WHERE "
            + " AND ".join(clauses)
            + " ORDER BY valid_from DESC, id DESC"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_as_fact(row) for row in rows]

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        now: float | None = None,
        include_pending: bool = False,
    ) -> list[Fact]:
        needle = (query or "").strip()
        if not needle:
            return []
        now = time.time() if now is None else now
        clauses, params = self._window(now, include_pending)
        with self._connect() as conn:
            match = _match(needle)
            if self.fts_ready and match is not None:
                sql = (
                    "SELECT f.* FROM facts_fts JOIN facts AS f"
                    " ON f.id = facts_fts.rowid"
                    " WHERE facts_fts MATCH ? AND "
                    + " AND ".join(clauses)
                    + " ORDER BY f.created_at DESC, f.id DESC LIMIT ?"
                )
                try:
                    rows = conn.execute(
                        sql, [match, *params, limit]
                    ).fetchall()
                    return [_as_fact(row) for row in rows]
                except sqlite3.OperationalError:
                    # A bad MATCH costs the index, not the answer: fall
                    # through to a literal LIKE over the same window.
                    pass
            like = f"%{needle}%"
            sql = (
                "SELECT * FROM facts WHERE (text LIKE ? OR value LIKE ?"
                " OR predicate LIKE ?) AND "
                + " AND ".join(clauses)
                + " ORDER BY created_at DESC, id DESC LIMIT ?"
            )
            rows = conn.execute(
                sql, [like, like, like, *params, limit]
            ).fetchall()
        return [_as_fact(row) for row in rows]

    def confirm(self, fact_id: int) -> bool:
        # A rejected fact stays rejected; confirming it again is not how
        # the owner changes their mind — a new fact with a source is.
        with self._connect() as conn, conn:
            cursor = conn.execute(
                "UPDATE facts SET status = ? WHERE id = ? AND status != ?",
                (CONFIRMED, fact_id, REJECTED),
            )
        return cursor.rowcount > 0

    def reject(self, fact_id: int) -> bool:
        with self._connect() as conn, conn:
            cursor = conn.execute(
                "UPDATE facts SET status = ? WHERE id = ?",
                (REJECTED, fact_id),
            )
        return cursor.rowcount > 0

    def supersede(self, old_id: int, new: Fact) -> int:
        with self._connect() as conn, conn:
            old = conn.execute(
                "SELECT id FROM facts WHERE id = ?", (old_id,)
            ).fetchone()
            if old is not None:
                conn.execute(
                    "UPDATE facts SET valid_to = ? WHERE id = ?",
                    (new.valid_from, old_id),
                )
            existing = self._source_fact(conn, new.source_ids)
            if existing is not None:
                return existing
            return self._insert(conn, replace(new, supersedes=old_id))

    def forget(self, fact_id: int) -> bool:
        with self._connect() as conn, conn:
            cursor = conn.execute(
                "UPDATE facts SET status = ?, valid_to = ? WHERE id = ?",
                (REJECTED, time.time(), fact_id),
            )
        return cursor.rowcount > 0
