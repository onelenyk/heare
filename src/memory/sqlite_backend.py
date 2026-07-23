"""SQLite + FTS5 memory backend.

Implements MemoryBackend ABC using aiosqlite with full-text search
via FTS5 content-sync virtual tables.  Triggers keep the FTS index in
sync with the memories table automatically.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import aiosqlite

from src.memory.base import MemoryBackend, MemoryEntry, MemoryType


class SQLiteBackend(MemoryBackend):
    """Persistent memory backend backed by SQLite with FTS5 full-text search."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Connect to the SQLite database, enable WAL mode, and create tables."""
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")

        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'user_stated',
                confidence REAL DEFAULT 1.0,
                created_ts REAL NOT NULL,
                last_accessed REAL NOT NULL DEFAULT 0,
                access_count INTEGER DEFAULT 0,
                archived INTEGER DEFAULT 0,
                metadata TEXT NOT NULL DEFAULT '{}'
            )
            """
        )

        await self._db.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                content,
                content='memories',
                content_rowid='rowid'
            )
            """
        )

        # Triggers to keep the FTS5 index in sync with the memories table.
        await self._db.execute(
            """
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
            END
            """
        )
        await self._db.execute(
            """
            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content)
                VALUES ('delete', old.rowid, old.content);
            END
            """
        )
        await self._db.execute(
            """
            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content)
                VALUES ('delete', old.rowid, old.content);
                INSERT INTO memories_fts(rowid, content)
                VALUES (new.rowid, new.content);
            END
            """
        )

        await self._db.commit()

    async def close(self) -> None:
        """Close the aiosqlite connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def store(self, entry: MemoryEntry) -> str:
        """Persist a memory entry.  Auto-generates an ID if empty.

        Returns the memory ID.
        """
        if not entry.id:
            entry.id = uuid.uuid4().hex
        if entry.created_ts == 0.0:
            entry.created_ts = time.time()

        await self._db.execute(
            """
            INSERT INTO memories (id, type, content, source, confidence,
                                  created_ts, last_accessed, access_count,
                                  archived, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                entry.id,
                entry.type.value,
                entry.content,
                entry.source,
                entry.confidence,
                entry.created_ts,
                entry.last_accessed,
                getattr(entry, "access_count", 0),
                json.dumps(entry.metadata),
            ),
        )
        await self._db.commit()
        return entry.id

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        types: list[MemoryType] | None = None,
    ) -> list[MemoryEntry]:
        """Search memories via FTS5 full-text match, with optional type filter.

        Results are ranked by BM25 relevance (FTS5 ``rank``) boosted by
        recency and frequency: recently-accessed and frequently-accessed
        memories sort higher.
        """
        sanitized = _sanitize_fts_query(query)
        params: list = [sanitized]

        type_clause = ""
        if types:
            placeholders = ", ".join(["?" for _ in types])
            type_clause = f" AND m.type IN ({placeholders})"
            params.extend(t.value for t in types)

        params.append(limit)

        sql = f"""
            SELECT m.id, m.type, m.content, m.source, m.confidence,
                   m.created_ts, m.last_accessed, m.access_count, m.metadata
            FROM memories m
            JOIN memories_fts fts ON m.rowid = fts.rowid
            WHERE memories_fts MATCH ? AND m.archived = 0
            {type_clause}
            ORDER BY rank * (
                1.0
                - 0.3 * MAX(0.0, 1.0 - (strftime('%%s','now') - m.last_accessed) / 2592000.0)
                - 0.1 * CAST(MIN(m.access_count, 5) AS REAL) / 5.0
            ) ASC
            LIMIT ?
        """
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [_row_to_entry(row) for row in rows]

    async def context(
        self,
        query: str | None = None,
        *,
        limit: int = 5,
    ) -> list[MemoryEntry]:
        """Retrieve contextually relevant memories, updating access metadata."""
        if query:
            entries = await self.search(query, limit=limit)
        else:
            entries = await self._get_recent(limit)

        now = time.time()
        for entry in entries:
            await self._db.execute(
                "UPDATE memories SET last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
                (now, entry.id),
            )
            entry.last_accessed = now
        await self._db.commit()

        return entries

    async def forget(self, memory_id: str) -> bool:
        """Soft-delete a memory by setting archived=1. Returns True if found."""
        cursor = await self._db.execute(
            "UPDATE memories SET archived = 1 WHERE id = ?",
            (memory_id,),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def stats(self) -> dict:
        """Return statistics: total, by_type, and archived counts."""
        cursor = await self._db.execute("SELECT COUNT(*) FROM memories")
        total = (await cursor.fetchone())[0]

        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM memories WHERE archived = 1"
        )
        archived = (await cursor.fetchone())[0]

        cursor = await self._db.execute(
            "SELECT type, COUNT(*) FROM memories GROUP BY type"
        )
        rows = await cursor.fetchall()
        by_type: dict[str, int] = {row[0]: row[1] for row in rows}

        return {"total": total, "by_type": by_type, "archived": archived}

    async def _get_recent(self, limit: int) -> list[MemoryEntry]:
        cursor = await self._db.execute(
            """SELECT id, type, content, source, confidence, created_ts,
                      last_accessed, access_count, metadata
               FROM memories
               WHERE archived = 0
               ORDER BY created_ts DESC
               LIMIT ?""",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [_row_to_entry(row) for row in rows]


def _sanitize_fts_query(raw: str) -> str:
    """Wrap each token in double quotes to prevent FTS5 syntax errors."""
    tokens = raw.split()
    if not tokens:
        return '""'
    return " ".join(f'"{t}"' for t in tokens)


def _row_to_entry(row: tuple) -> MemoryEntry:
    return MemoryEntry(
        id=row[0],
        type=MemoryType(row[1]),
        content=row[2],
        source=row[3],
        confidence=row[4],
        created_ts=row[5],
        last_accessed=row[6],
        metadata=json.loads(row[8]) if row[8] else {},
    )
