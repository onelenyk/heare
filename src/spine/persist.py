"""The spine's memory of what was said, in the same DB the daemon uses.

`~/.heare/heare.db` is owned by `src/store/storage.py`'s `TranscriptStore`,
which talks to it via `aiosqlite` because the audio pipeline must never
block on disk. The spine has no such pipeline to protect: its contract
calls for plain synchronous methods a conductor can dispatch through
`asyncio.to_thread`. Mixing an aiosqlite connection (bound to whichever
event loop created it) into a thread-pool caller is the kind of thing
that works in a demo and deadlocks in production, so this module opens
its own stdlib `sqlite3` connection instead — same file, same schema,
same WAL mode, just a different (synchronous) door into it.

"Same schema" is not a suggestion: `SCHEMA` and `SCHEMA_VERSION` are
imported straight from storage.py rather than retyped here, so the two
modules can never drift into competing table definitions. Every access
to the connection is taken under a lock, since a shared `sqlite3.Connection`
handed to a thread pool has no other guarantee that two turns won't
interleave their writes.

`transcripts.turn_id` and the `turns` table itself have existed in the
schema since schema v8 but nothing ever wrote to them (`turns` was empty,
`transcripts.turn_id` was always NULL) — the daemon logs transcripts as a
flat stream with no turn grouping. The spine's `TurnAssembler`
(src/spine/turn.py) is the first thing in this codebase that actually
knows where one user turn ends and the next begins, so this is the first
code to make those columns real.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from src.store.storage import SCHEMA, SCHEMA_VERSION


class SpinePersistence:
    """The spine's memory of what was said — same DB as the daemon."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # check_same_thread=False: the conductor calls these methods via
        # asyncio.to_thread, which may run different calls on different
        # worker threads over the lifetime of this object. self._lock
        # (held for the duration of every method below) is what keeps
        # that safe, not this flag alone.
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        # Reuses storage.py's own schema script verbatim (see module
        # docstring) — idempotent, so this is safe whether the DB is
        # brand new or already at schema v8.
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._ensure_schema_version_row()

    def _ensure_schema_version_row(self) -> None:
        """Mirror storage.py's `_check_schema_version` for a fresh DB only.

        If a `meta.schema_version` row already exists, the daemon's own
        async migrations are the authority on it — this module never
        rewrites it, only fills it in the first time so a DB created by
        the spine alone (no daemon ever run) still self-describes.
        """
        cur = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", ("schema_version",)
        )
        if cur.fetchone() is None:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )
            self._conn.commit()

    def _active_conversation_id(self) -> int:
        """Get-or-create the open (end_ts IS NULL) conversation.

        `turns.conversation_id` is NOT NULL, so every turn needs one.
        This mirrors storage.py's `get_active_conversation` /
        `start_conversation` pair (storage.py:494-533) as raw sync SQL —
        those methods are async (aiosqlite) and this module is
        deliberately synchronous, so reusing them directly isn't
        possible, but the read-then-insert logic and the table it
        touches are identical to the daemon's.
        """
        cur = self._conn.execute(
            "SELECT id FROM conversations WHERE end_ts IS NULL "
            "ORDER BY start_ts DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row is not None:
            return int(row[0])
        cur = self._conn.execute(
            "INSERT INTO conversations (start_ts, mode) VALUES (?, ?)",
            (time.time(), "spine"),
        )
        assert cur.lastrowid is not None
        return cur.lastrowid

    def log_user_turn(self, text: str, fragment_count: int = 1) -> int:
        """Insert the user turn: a transcripts row (mode='spine',
        agent_spoken=0) AND a turns row (aggregated_text=text,
        utterance_count=fragment_count, start_ts/end_ts=now), linking
        transcripts.turn_id to the new turn. Returns turn_id."""
        with self._lock:
            now = time.time()
            conversation_id = self._active_conversation_id()
            cur = self._conn.execute(
                "INSERT INTO turns "
                "(conversation_id, start_ts, end_ts, aggregated_text, utterance_count) "
                "VALUES (?, ?, ?, ?, ?)",
                (conversation_id, now, now, text, fragment_count),
            )
            turn_id = cur.lastrowid
            assert turn_id is not None
            self._conn.execute(
                "INSERT INTO transcripts (ts, text, mode, agent_spoken, turn_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (now, text, "spine", 0, turn_id),
            )
            self._conn.commit()
            return turn_id

    def log_agent_reply(self, text: str, turn_id: int) -> None:
        """Insert the reply as transcripts row (agent_spoken=1) with the
        same turn_id, and stamp turns.end_ts=now."""
        with self._lock:
            now = time.time()
            self._conn.execute(
                "INSERT INTO transcripts (ts, text, mode, agent_spoken, turn_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (now, text, "spine", 1, turn_id),
            )
            self._conn.execute(
                "UPDATE turns SET end_ts = ? WHERE id = ?", (now, turn_id)
            )
            self._conn.commit()

    def recent_exchanges(self, n: int = 6) -> list[dict]:
        """Last n closed turns as [{"user": ..., "agent": ...}] oldest
        first, read via turn_id joins — for the prompt context."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT id FROM turns ORDER BY id DESC LIMIT ?", (n,)
            )
            turn_ids = [row[0] for row in cur.fetchall()]
            turn_ids.reverse()

            exchanges: list[dict] = []
            for turn_id in turn_ids:
                cur = self._conn.execute(
                    "SELECT text, agent_spoken FROM transcripts "
                    "WHERE turn_id = ? ORDER BY ts ASC",
                    (turn_id,),
                )
                user_text: str | None = None
                agent_text: str | None = None
                for text, agent_spoken in cur.fetchall():
                    if agent_spoken:
                        agent_text = text
                    else:
                        user_text = text
                exchanges.append({"user": user_text, "agent": agent_text})
            return exchanges

    def close(self) -> None:
        with self._lock:
            self._conn.close()
