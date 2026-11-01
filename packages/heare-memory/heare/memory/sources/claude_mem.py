"""claude-mem: what it observed of coding sessions.

It watches code, so nothing here counts as personal: it can be searched, and is
not offered to the import until a look at real rows says otherwise.
"""

from __future__ import annotations

import sqlite3

from heare.memory.sources import TEXT_CHARS, Record, SQLiteSource


class ClaudeMem(SQLiteSource):
    name = "claude-mem"
    needs = frozenset({"id", "project", "type", "title", "narrative", "created_at_epoch"})
    fts = "observations_fts"

    def _record(self, row: sqlite3.Row) -> Record:
        text = f"{row['title'] or ''}\n{row['narrative'] or ''}".strip()
        return Record(f"claude-mem:{row['id']}", text[:TEXT_CHARS], str(row["project"] or ""),
                      str(row["type"] or "note"), float(row["created_at_epoch"] or 0) / 1000, False)
