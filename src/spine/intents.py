"""What the assistant means to say, and has not said yet.

A job is work it was given. An intent is something it means to raise —
and the two are different in the way that matters here: a job is finished
when the work is done, an intent is finished only when it has landed with
a person, at a moment that suited them.

Until now nothing of the kind existed. Every turn was assembled from a
prompt, answered, and thrown away; between one utterance and the next
there was no object holding anything at all. The assistant could not want
to say something, because it had nowhere to keep the wanting.

An intent is a row, in the same database as the jobs, for the same
reason: it has to survive the process. Two properties follow, and the
second matters more than it looks.

* **It can be raised later.** "The thing you asked me to check finished
  while you were out" is only possible if the finishing was recorded
  somewhere that outlives the moment.
* **An intent never voiced is still worth holding.** When the user speaks
  first, what is pending goes into the prompt — so the assistant answers
  knowing what is outstanding between them, rather than from a blank
  page. Speaking unbidden is the visible part; this is the ground under
  it.

``origin`` separates the two kinds. ``user`` is something it was asked
for. ``self`` is something it noticed on its own — and that is the part
that makes it possible to see what it wants.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("heare.intents")

SCHEMA = """
CREATE TABLE IF NOT EXISTS intents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    origin TEXT NOT NULL,
    urgency REAL NOT NULL DEFAULT 0.5,
    state TEXT NOT NULL,
    created_ts REAL NOT NULL,
    updated_ts REAL NOT NULL,
    due_ts REAL,
    voiced_ts REAL,
    outcome TEXT,
    dedupe_key TEXT
);
CREATE INDEX IF NOT EXISTS idx_intents_state ON intents(state);
CREATE INDEX IF NOT EXISTS idx_intents_created ON intents(created_ts DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_intents_dedupe
    ON intents(dedupe_key) WHERE dedupe_key IS NOT NULL;
"""

# Where it came from.
USER = "user"  # asked for it
SELF = "self"  # noticed it

# Where it is.
PENDING = "pending"
VOICED = "voiced"
DONE = "done"
DROPPED = "dropped"

# How it landed. Feeds the engine's trust, which is what keeps "speak
# freely" from becoming "speak constantly".
ACCEPTED = "accepted"  # answered on the subject
IGNORED = "ignored"  # talked past it, or said nothing
REJECTED = "rejected"  # told to leave it

# A voiced intent nobody reacted to stops meaning anything. Left pending
# it would be raised again on the next quiet moment, which is exactly how
# an assistant becomes a nag.
STALE_AFTER_S = 24 * 3600


@dataclass
class Intent:
    id: int
    kind: str
    text: str
    origin: str
    urgency: float
    state: str
    created_ts: float
    updated_ts: float
    due_ts: float | None = None
    voiced_ts: float | None = None
    outcome: str | None = None
    dedupe_key: str | None = None

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.created_ts)

    @property
    def is_mine(self) -> bool:
        """Nobody asked for this one."""
        return self.origin == SELF

    def ripe(self, now: float | None = None) -> bool:
        """Some things are worth saying, but not yet."""
        if self.due_ts is None:
            return True
        return (now if now is not None else time.time()) >= self.due_ts

    def describe(self) -> str:
        """One line, for reading aloud."""
        return f"{_ago(self.age_seconds)}: {self.text}"


def _ago(seconds: float) -> str:
    if seconds < 90:
        return "щойно"
    if seconds < 3600:
        return f"{int(seconds // 60)} хв тому"
    if seconds < 24 * 3600:
        return f"{int(seconds // 3600)} год тому"
    return f"{int(seconds // 86400)} дн тому"


class IntentStore:
    """Persistence for what it means to say. Never raises into the caller.

    Every method swallows its exceptions for the same reason the job store
    does: a bookkeeping failure must not be able to stop the assistant
    talking. A dropped row costs one raised subject; an exception on the
    conversational path costs the conversation.
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    async def init(self) -> None:
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def add(
        self,
        kind: str,
        text: str,
        *,
        origin: str = SELF,
        urgency: float = 0.5,
        due_ts: float | None = None,
        dedupe_key: str | None = None,
    ) -> int | None:
        """Form an intent. Cheap on purpose — voicing is the expensive part.

        ``dedupe_key`` makes noticing idempotent: the engine re-reads the
        same finished job on every tick, and without this each pass would
        add another copy of the same thing to say.
        """
        now = time.time()
        try:
            cursor = await self._db.execute(
                "INSERT OR IGNORE INTO intents "
                "(kind, text, origin, urgency, state, created_ts, updated_ts, "
                " due_ts, dedupe_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    kind,
                    text[:500],
                    origin,
                    urgency,
                    PENDING,
                    now,
                    now,
                    due_ts,
                    dedupe_key,
                ),
            )
            await self._db.commit()
            return cursor.lastrowid or None
        except Exception:
            logger.exception("intents: add failed (non-fatal)")
            return None

    async def pending(self, limit: int = 10) -> list[Intent]:
        """Outstanding, most urgent first, then oldest."""
        try:
            cursor = await self._db.execute(
                "SELECT * FROM intents WHERE state = ? "
                "ORDER BY urgency DESC, created_ts ASC LIMIT ?",
                (PENDING, limit),
            )
            return [_row_to_intent(r) for r in await cursor.fetchall()]
        except Exception:
            logger.exception("intents: pending failed (non-fatal)")
            return []

    async def mark_voiced(self, intent_id: int | None) -> None:
        if intent_id is None:
            return
        now = time.time()
        try:
            await self._db.execute(
                "UPDATE intents SET state = ?, voiced_ts = ?, updated_ts = ? "
                "WHERE id = ?",
                (VOICED, now, now, intent_id),
            )
            await self._db.commit()
        except Exception:
            logger.exception("intents: mark_voiced failed (non-fatal)")

    async def settle(self, intent_id: int | None, outcome: str) -> None:
        """How it landed. ``accepted`` closes it; anything else leaves it
        closed too — raising the same subject twice is what makes an
        assistant tiresome, and one attempt is enough to have tried."""
        if intent_id is None:
            return
        try:
            await self._db.execute(
                "UPDATE intents SET state = ?, outcome = ?, updated_ts = ? "
                "WHERE id = ?",
                (DONE if outcome == ACCEPTED else DROPPED, outcome, time.time(), intent_id),
            )
            await self._db.commit()
        except Exception:
            logger.exception("intents: settle failed (non-fatal)")

    async def drop(self, intent_id: int | None, reason: str = "") -> None:
        if intent_id is None:
            return
        try:
            await self._db.execute(
                "UPDATE intents SET state = ?, outcome = ?, updated_ts = ? "
                "WHERE id = ?",
                (DROPPED, reason or None, time.time(), intent_id),
            )
            await self._db.commit()
        except Exception:
            logger.exception("intents: drop failed (non-fatal)")

    async def awaiting_reaction(self) -> Intent | None:
        """The most recently voiced one still waiting to see how it landed."""
        try:
            cursor = await self._db.execute(
                "SELECT * FROM intents WHERE state = ? AND outcome IS NULL "
                "ORDER BY voiced_ts DESC LIMIT 1",
                (VOICED,),
            )
            row = await cursor.fetchone()
            return _row_to_intent(row) if row else None
        except Exception:
            logger.exception("intents: awaiting_reaction failed (non-fatal)")
            return None

    async def sweep_stale(self, now: float | None = None) -> int:
        """Voiced a day ago and never answered. Nobody is coming back to it."""
        cutoff = (now if now is not None else time.time()) - STALE_AFTER_S
        try:
            cursor = await self._db.execute(
                "UPDATE intents SET state = ?, outcome = ?, updated_ts = ? "
                "WHERE state = ? AND outcome IS NULL AND voiced_ts < ?",
                (DROPPED, IGNORED, time.time(), VOICED, cutoff),
            )
            await self._db.commit()
            n = cursor.rowcount or 0
            if n:
                logger.info("intents: %d went unanswered and were let go", n)
            return n
        except Exception:
            logger.exception("intents: sweep failed (non-fatal)")
            return 0

    async def recent(self, limit: int = 5) -> list[Intent]:
        try:
            cursor = await self._db.execute(
                "SELECT * FROM intents ORDER BY created_ts DESC LIMIT ?", (limit,)
            )
            return [_row_to_intent(r) for r in await cursor.fetchall()]
        except Exception:
            logger.exception("intents: recent failed (non-fatal)")
            return []


def _row_to_intent(row: Any) -> Intent:
    return Intent(
        id=row[0],
        kind=row[1],
        text=row[2],
        origin=row[3],
        urgency=row[4],
        state=row[5],
        created_ts=row[6],
        updated_ts=row[7],
        due_ts=row[8],
        voiced_ts=row[9],
        outcome=row[10],
        dedupe_key=row[11],
    )
