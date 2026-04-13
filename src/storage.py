"""SQLite transcript + decision store via aiosqlite so the audio pipeline never blocks on disk."""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

import aiosqlite


SCHEMA_VERSION = 2

logger = logging.getLogger("heare.storage")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    text TEXT NOT NULL,
    mode TEXT NOT NULL,
    speaker_id TEXT,
    speaker_confidence REAL
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    transcript_id INTEGER,
    type TEXT NOT NULL,
    confidence REAL,
    reason TEXT,
    reply TEXT,
    intent TEXT,
    action_json TEXT,
    FOREIGN KEY(transcript_id) REFERENCES transcripts(id)
);

CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    decision_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    result_summary TEXT,
    FOREIGN KEY(decision_id) REFERENCES decisions(id)
);

CREATE TABLE IF NOT EXISTS heartbeats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    decided_to_speak INTEGER NOT NULL,
    reply TEXT
);

CREATE INDEX IF NOT EXISTS idx_transcripts_ts ON transcripts(ts DESC);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts DESC);
"""


class TranscriptStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        await self._migrate_speaker_columns()
        await self._check_schema_version()

    async def _migrate_speaker_columns(self) -> None:
        # Idempotent ALTER — upgrades older DBs created before SPK-001 where
        # the transcripts table did not have speaker_id/speaker_confidence.
        # Fresh installs already have the columns via SCHEMA above.
        for col_ddl in (
            "ALTER TABLE transcripts ADD COLUMN speaker_id TEXT",
            "ALTER TABLE transcripts ADD COLUMN speaker_confidence REAL",
        ):
            try:
                await self.db.execute(col_ddl)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
        await self.db.commit()

    async def _check_schema_version(self) -> None:
        cursor = await self.db.execute(
            "SELECT value FROM meta WHERE key = ?", ("schema_version",)
        )
        row = await cursor.fetchone()
        if row is None:
            await self.db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )
            await self.db.commit()
            return
        existing = int(row[0])
        if existing > SCHEMA_VERSION:
            raise RuntimeError(
                f"heare DB schema_version={existing} is newer than this code "
                f"(expected {SCHEMA_VERSION}). Refusing to open — please upgrade heare."
            )
        if existing < SCHEMA_VERSION:
            await self.db.execute(
                "UPDATE meta SET value = ? WHERE key = ?",
                (str(SCHEMA_VERSION), "schema_version"),
            )
            await self.db.commit()

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("TranscriptStore.init() must be called first")
        return self._db

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def log_transcript(
        self,
        text: str,
        mode: str,
        speaker_id: str | None = None,
        speaker_confidence: float | None = None,
    ) -> int:
        cursor = await self.db.execute(
            "INSERT INTO transcripts (ts, text, mode, speaker_id, speaker_confidence)"
            " VALUES (?, ?, ?, ?, ?)",
            (time.time(), text, mode, speaker_id, speaker_confidence),
        )
        await self.db.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def log_decision(
        self,
        transcript_id: int | None,
        decision: dict[str, Any],
    ) -> int:
        cursor = await self.db.execute(
            """
            INSERT INTO decisions
                (ts, transcript_id, type, confidence, reason, reply, intent, action_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                transcript_id,
                decision.get("type", "nothing"),
                decision.get("confidence"),
                decision.get("reason"),
                decision.get("reply"),
                decision.get("intent"),
                json.dumps(decision.get("action")) if decision.get("action") else None,
            ),
        )
        await self.db.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def log_action(
        self, decision_id: int, status: str, result_summary: str | None
    ) -> int:
        cursor = await self.db.execute(
            "INSERT INTO actions (ts, decision_id, status, result_summary) VALUES (?, ?, ?, ?)",
            (time.time(), decision_id, status, result_summary),
        )
        await self.db.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def log_heartbeat(self, decided_to_speak: bool, reply: str | None) -> int:
        cursor = await self.db.execute(
            "INSERT INTO heartbeats (ts, decided_to_speak, reply) VALUES (?, ?, ?)",
            (time.time(), 1 if decided_to_speak else 0, reply),
        )
        await self.db.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def recent_transcripts(self, n: int = 5) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            "SELECT id, ts, text, mode FROM transcripts ORDER BY ts DESC LIMIT ?",
            (n,),
        )
        rows = await cursor.fetchall()
        return [
            {"id": r[0], "ts": r[1], "text": r[2], "mode": r[3]}
            for r in reversed(rows)
        ]

    async def purge_older_than(self, days: int) -> int:
        cutoff = time.time() - (days * 86400)
        cursor = await self.db.execute("DELETE FROM transcripts WHERE ts < ?", (cutoff,))
        await self.db.commit()
        return cursor.rowcount or 0
