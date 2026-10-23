"""Bring the old ``memories`` table into the typed fact store.

The table is the deleted engine's memory: a row is a sentence, a type and
when it was written. The new store wants a fact with provenance, so the
migration is deliberately narrow — one subject, a predicate built from the
old type, and the row's words as both value and text. The row id travels
as a source id, which is what makes a second run a no-op.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from heare.memory.contracts import (
    LEGACY,
    PENDING,
    Fact,
    FactStore,
    MigrationReport,
)

_COLUMNS = "id, type, content, source, confidence, created_ts, archived"


def migrate(
    legacy_db: str | Path,
    store: FactStore,
    *,
    include_archived: bool = False,
    dry_run: bool = False,
) -> MigrationReport:
    """Copy the old table row by row, skipping what is already known.

    The old database is opened ``mode=ro``: a migration reads the past and
    must not be able to rewrite it if a fact turns out to be malformed.
    """
    path = Path(legacy_db).expanduser()
    if not path.exists():
        return MigrationReport(dry_run=dry_run)

    uri = f"{path.resolve().as_uri()}?mode=ro"
    read = imported = archived = duplicates = 0

    with closing(sqlite3.connect(uri, uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        if not _has_memories(conn):
            return MigrationReport(dry_run=dry_run)

        rows = conn.execute(f"SELECT {_COLUMNS} FROM memories")
        for row in rows:
            read += 1
            if row["archived"] and not include_archived:
                archived += 1
                continue

            content = row["content"] or ""
            if not content.strip():
                continue

            source_id = f"legacy:{row['id']}"
            if store.has_source(source_id):
                duplicates += 1
                continue

            if not dry_run:
                store.add(_fact(row, content, source_id))
            imported += 1

    return MigrationReport(
        read=read,
        imported=imported,
        archived=archived,
        duplicates=duplicates,
        dry_run=dry_run,
    )


def _has_memories(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memories'"
    ).fetchone()
    return row is not None


def _fact(row: sqlite3.Row, content: str, source_id: str) -> Fact:
    created = float(row["created_ts"] or 0.0)
    confidence = 0.0 if row["confidence"] is None else float(row["confidence"])
    return Fact(
        subject="owner",
        predicate=f"legacy_{row['type']}",
        value=content,
        text=content,
        source=LEGACY,
        trust=LEGACY,
        status=PENDING,
        confidence=min(1.0, max(0.0, confidence)),
        valid_from=created,
        created_at=created,
        source_ids=(source_id,),
    )
