"""Engram: what an agent noted while it worked, kept by a program of its own."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from heare.memory.sources import TEXT_CHARS, Record, SQLiteSource

PERSONAL_KINDS = {"decision", "pattern"}


class Engram(SQLiteSource):
    name = "engram"
    needs = frozenset({"id", "type", "title", "content", "project", "created_at", "deleted_at"})
    fts = "observations_fts"

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        # Engram names a project after its folder: the one named after the
        # home folder is the person, the rest are their code.
        self.owner = path.parent.parent.name

    def _live(self) -> str:
        return "deleted_at IS NULL"

    def _record(self, row: sqlite3.Row) -> Record:
        project, kind = str(row["project"] or ""), str(row["type"] or "note")
        text = f"{row['title'] or ''}\n{row['content'] or ''}".strip()
        try:
            at = datetime.fromisoformat(str(row["created_at"])).timestamp()
        except ValueError:
            at = 0.0
        return Record(f"engram:{row['id']}", text[:TEXT_CHARS], project, kind, at,
                      project == self.owner and kind in PERSONAL_KINDS)
