"""Unified state store — SQLite + memory cache."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

import aiosqlite

logger = logging.getLogger("heare.state")


class State:
    """Thread-safe state store with memory cache + SQLite persistence."""

    def __init__(self, db_path: Path | str):
        self._db_path = Path(db_path)
        self._cache: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def init(self):
        """Create table and load existing state."""
        async with self._lock:
            async with aiosqlite.connect(str(self._db_path)) as db:
                await db.execute(
                    "CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)"
                )
                await db.commit()
                rows = await db.execute_fetchall("SELECT key, value FROM state")
                self._cache = {row[0]: row[1] for row in rows}
        logger.info("State loaded: %d keys", len(self._cache))
        # Held outside the lock: _migrate_legacy acquires lock internally via set_bulk
        await self._migrate_legacy()
        await self._ensure_canvas_rendered_column()

    # ── Getters (sync — pipeline safe) ──────────────────────

    def get(self, key: str, default: str = "") -> str:
        return self._cache.get(key, default)

    def get_bool(self, key: str) -> bool:
        return self._cache.get(key) == "1"

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self._cache.get(key, str(default)))
        except ValueError:
            return default

    # ── Setters (async — API calls) ────────────────────────

    async def set(self, key: str, value: str):
        async with self._lock:
            self._cache[key] = value
            async with aiosqlite.connect(str(self._db_path)) as db:
                await db.execute(
                    "INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)",
                    (key, value),
                )
                await db.commit()

    async def set_bool(self, key: str, value: bool):
        await self.set(key, "1" if value else "0")

    async def set_bulk(self, items: dict[str, str]):
        async with self._lock:
            self._cache.update(items)
            async with aiosqlite.connect(str(self._db_path)) as db:
                await db.executemany(
                    "INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)",
                    list(items.items()),
                )
                await db.commit()

    # ── Snapshot ────────────────────────────────────────────

    def snapshot(self) -> dict:
        """Return full state dict (for GET /state)."""
        return dict(self._cache)

    # ── Migration ──────────────────────────────────────────

    async def _migrate_legacy(self):
        """Import existing state from legacy flag/text files on first load."""
        if self._cache:
            return

        h = Path.home() / ".heare"
        migrated = {}

        for key, filename in [
            ("mute_bot", "mute.flag"),
            ("mute_mic", "mute_input.flag"),
        ]:
            if (h / filename).exists():
                migrated[key] = "1"

        for key, filename in [
            ("mode", "mode"),
            ("provider", "provider"),
            ("model", "model"),
        ]:
            path = h / filename
            if path.exists():
                migrated[key] = path.read_text().strip()

        if migrated:
            await self.set_bulk(migrated)
            logger.info("State migrated from legacy files: %s", list(migrated.keys()))

    async def _ensure_canvas_rendered_column(self):
        """Add rendered column to displays table if missing."""
        try:
            async with aiosqlite.connect(str(self._db_path)) as db:
                await db.execute(
                    "ALTER TABLE displays ADD COLUMN rendered INTEGER DEFAULT 0"
                )
                await db.commit()
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise

    async def get_latest_canvas(self) -> dict | None:
        """Return latest unrendered canvas or None."""
        async with aiosqlite.connect(str(self._db_path)) as db:
            row = await db.execute_fetchall(
                "SELECT content, ts, COALESCE(rendered,0) AS rendered FROM displays "
                "WHERE content_type='canvas/html' AND COALESCE(rendered,0)=0 "
                "ORDER BY ts DESC LIMIT 1"
            )
            if row:
                await db.execute(
                    "UPDATE displays SET rendered=1 WHERE ts=?", (row[0][1],)
                )
                await db.commit()
                return {"html": row[0][0], "ts": row[0][1], "rendered": False}
            return None
