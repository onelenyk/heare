"""Delegated work that outlives the turn — and the process.

Until now a job existed only as an asyncio task. It began, it ended, and
nothing remained: the assistant could not say what it did yesterday, and
a restart mid-job left the user waiting for an answer that could never
arrive. That is the difference between carrying out an instruction and
running an errand.

A job is a row. It records the goal in the user's own words, what the
worker did, and how it ended. Two things follow that were impossible
before:

* **A restart is survivable.** Jobs still marked ``running`` at startup
  were cut off by the process ending. They are marked ``interrupted``
  and can be reported: "I was checking the disk when I stopped."
* **The worker can be asked.** ``recent`` answers "what were you doing?"
  from the record rather than from a context window that has since
  rolled over.

Deliberately *not* here: automatic resumption. Replaying an arbitrary
sequence of shell commands after a crash is not safe, and pretending
otherwise would be worse than admitting the interruption.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("heare.jobs")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    task TEXT NOT NULL,
    state TEXT NOT NULL,
    created_ts REAL NOT NULL,
    updated_ts REAL NOT NULL,
    steps_json TEXT NOT NULL DEFAULT '[]',
    result TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_ts DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
"""

RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"
INTERRUPTED = "interrupted"


@dataclass
class Job:
    id: int
    label: str
    task: str
    state: str
    created_ts: float
    updated_ts: float
    steps: list[str] = field(default_factory=list)
    result: str | None = None
    error: str | None = None

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.created_ts)

    def describe(self) -> str:
        """One line, for reading aloud."""
        when = _ago(self.age_seconds)
        if self.state == DONE:
            return f"{when}: {self.task} — {self.result or 'готово'}"
        if self.state == INTERRUPTED:
            return f"{when}: {self.task} — обірвалось на перезапуску"
        if self.state == RUNNING:
            return f"{when}: {self.task} — ще в роботі"
        if self.state == CANCELLED:
            return f"{when}: {self.task} — скасовано"
        return f"{when}: {self.task} — не вдалося: {self.error or 'без причини'}"


def _ago(seconds: float) -> str:
    if seconds < 90:
        return "щойно"
    if seconds < 3600:
        return f"{int(seconds // 60)} хв тому"
    if seconds < 86400:
        return f"{int(seconds // 3600)} год тому"
    return f"{int(seconds // 86400)} дн тому"


class JobStore:
    """Persistence for delegated work. Never raises into the caller."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def init(self) -> None:
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def start(self, label: str, task: str) -> int | None:
        now = time.time()
        try:
            cursor = await self._db.execute(
                "INSERT INTO jobs (label, task, state, created_ts, updated_ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (label, task, RUNNING, now, now),
            )
            await self._db.commit()
            return cursor.lastrowid
        except Exception:
            logger.exception("jobs: start failed (non-fatal)")
            return None

    async def step(self, job_id: int | None, description: str) -> None:
        """Append what the worker just did, so an interrupted job can say
        how far it got."""
        if job_id is None:
            return
        try:
            cursor = await self._db.execute(
                "SELECT steps_json FROM jobs WHERE id = ?", (job_id,)
            )
            row = await cursor.fetchone()
            steps = json.loads(row[0]) if row and row[0] else []
            steps.append(description[:300])
            await self._db.execute(
                "UPDATE jobs SET steps_json = ?, updated_ts = ? WHERE id = ?",
                (json.dumps(steps[-20:], ensure_ascii=False), time.time(), job_id),
            )
            await self._db.commit()
        except Exception:
            logger.exception("jobs: step failed (non-fatal)")

    async def finish(
        self,
        job_id: int | None,
        state: str,
        *,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        if job_id is None:
            return
        try:
            await self._db.execute(
                "UPDATE jobs SET state = ?, result = ?, error = ?, updated_ts = ? "
                "WHERE id = ?",
                (state, result, error, time.time(), job_id),
            )
            await self._db.commit()
        except Exception:
            logger.exception("jobs: finish failed (non-fatal)")

    async def sweep_interrupted(self) -> list[Job]:
        """Jobs still marked running at startup were cut off by the
        process ending — nothing else can leave them that way."""
        try:
            cursor = await self._db.execute(
                "SELECT * FROM jobs WHERE state = ? ORDER BY created_ts DESC",
                (RUNNING,),
            )
            rows = await cursor.fetchall()
            stranded = [_row_to_job(r) for r in rows]
            if stranded:
                await self._db.execute(
                    "UPDATE jobs SET state = ?, updated_ts = ? WHERE state = ?",
                    (INTERRUPTED, time.time(), RUNNING),
                )
                await self._db.commit()
                logger.info("jobs: %d interrupted by a restart", len(stranded))
            return stranded
        except Exception:
            logger.exception("jobs: sweep failed (non-fatal)")
            return []

    async def recent(self, limit: int = 5) -> list[Job]:
        try:
            cursor = await self._db.execute(
                "SELECT * FROM jobs ORDER BY created_ts DESC LIMIT ?", (limit,)
            )
            return [_row_to_job(r) for r in await cursor.fetchall()]
        except Exception:
            logger.exception("jobs: recent failed (non-fatal)")
            return []


def _row_to_job(row: Any) -> Job:
    return Job(
        id=row[0],
        label=row[1],
        task=row[2],
        state=row[3],
        created_ts=row[4],
        updated_ts=row[5],
        steps=json.loads(row[6]) if row[6] else [],
        result=row[7],
        error=row[8],
    )
