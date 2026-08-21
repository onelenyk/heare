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

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from src.store.storage import SCHEMA, SCHEMA_VERSION

logger = logging.getLogger("spine.persist")

# What `transcripts.source` says about a line. The column already held
# "voice" and "typed" from the old engine; these are the spine's two, and
# the distinction is the whole retention policy: one is a conversation,
# the other is the room.
ADDRESSED = "voice"  # said to the assistant, and answered — kept
OVERHEARD = "overheard"  # said near it, never a turn — expires

# The one table the spine owns alone. It is NOT in storage.py's SCHEMA and
# does NOT bump SCHEMA_VERSION: storage.py's `_check_schema_version` refuses
# to open a DB whose recorded version is *newer* than the code opening it,
# so raising the shared version to 9 here would make the same ~/.heare/heare.db
# unopenable by the pipecat store (still compiled against 8) — a rollback to
# `engine = "pipecat"` would then fail to boot. A plain additive
# CREATE TABLE IF NOT EXISTS is invisible to every reader that does not ask
# for it, needs no migration, and leaves the shared version alone.
ROLE_SESSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS role_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_name TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT '',
    started_ts REAL NOT NULL,
    ended_ts REAL,                       -- NULL = still live
    artifact_path TEXT,
    turn_ids TEXT NOT NULL DEFAULT '[]', -- JSON array of turns.id
    status TEXT NOT NULL DEFAULT 'live'  -- 'live' | 'done' | 'interrupted'
);
CREATE INDEX IF NOT EXISTS idx_role_sessions_live
    ON role_sessions(ended_ts);
"""


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
        # Additive, spine-only (see ROLE_SESSIONS_SCHEMA): executed the same
        # way — one idempotent script, right after the shared one.
        self._conn.executescript(ROLE_SESSIONS_SCHEMA)
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
                "INSERT INTO transcripts "
                "(ts, text, mode, agent_spoken, turn_id, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now, text, "spine", 0, turn_id, ADDRESSED),
            )
            self._conn.commit()
            return turn_id

    def log_agent_reply(self, text: str, turn_id: int) -> None:
        """Insert the reply as transcripts row (agent_spoken=1) with the
        same turn_id, and stamp turns.end_ts=now."""
        with self._lock:
            now = time.time()
            self._conn.execute(
                "INSERT INTO transcripts "
                "(ts, text, mode, agent_spoken, turn_id, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now, text, "spine", 1, turn_id, ADDRESSED),
            )
            self._conn.execute(
                "UPDATE turns SET end_ts = ? WHERE id = ?", (now, turn_id)
            )
            self._conn.commit()

    def last_turn_times(self) -> dict[str, float | None]:
        """When anything was last said, and when the user last said it.

        How long the two of you have been quiet was known to nothing:
        the transcripts carry a timestamp per row, and no one had ever
        needed to ask them the question. It is one query, and it is the
        difference between a reply and a remark.
        """
        with self._lock:
            cur = self._conn.execute("SELECT MAX(ts) FROM transcripts")
            any_ts = (cur.fetchone() or [None])[0]
            cur = self._conn.execute(
                "SELECT MAX(ts) FROM transcripts WHERE agent_spoken = 0"
            )
            user_ts = (cur.fetchone() or [None])[0]
        return {"any": any_ts, "user": user_ts}

    # -- what was said near it, but not to it --------------------------
    #
    # The microphone already hears the room and Whisper already
    # transcribes all of it — the wake gate decides whether to *act*, not
    # whether to listen. Until now an unaddressed line was thrown away
    # after being heard and paid for, which is why "he heard everything"
    # was never true of the database.
    #
    # Kept apart rather than mixed in, because the two are not the same
    # kind of record. Addressed speech is a conversation and stays.
    # Overheard speech is working memory: it is marked, it is never a
    # turn, and it expires.

    def log_overheard(self, text: str) -> None:
        """Write down something said near it. Never raises.

        No `turns` row: a turn is one exchange, and nothing answered
        this. It lands in `transcripts` alone, marked, so every reader
        that does not ask for it does not see it.
        """
        text = (text or "").strip()
        if not text:
            return
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO transcripts "
                    "(ts, text, mode, agent_spoken, source) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (time.time(), text, "spine", 0, OVERHEARD),
                )
                self._conn.commit()
        except Exception:  # noqa: BLE001
            logger.exception("persist: could not keep an overheard line")

    def forget_overheard(self, *, before_ts: float) -> int:
        """Drop overheard lines older than a moment. Returns how many.

        This is the difference between working memory and a recording
        you forgot you were making. Addressed speech is never touched:
        that was a conversation, and deleting it would be deleting your
        own words.
        """
        try:
            with self._lock:
                cur = self._conn.execute(
                    "DELETE FROM transcripts WHERE source = ? AND ts < ?",
                    (OVERHEARD, before_ts),
                )
                self._conn.commit()
                return cur.rowcount or 0
        except Exception:  # noqa: BLE001
            logger.exception("persist: forgetting failed (non-fatal)")
            return 0

    def forget_overheard_since(self, *, after_ts: float) -> int:
        """Drop overheard lines *newer* than a moment — "forget the last
        hour". The other direction, and the one a person asks for out
        loud, usually about something that has just been said in the
        room by someone who did not know."""
        try:
            with self._lock:
                cur = self._conn.execute(
                    "DELETE FROM transcripts WHERE source = ? AND ts >= ?",
                    (OVERHEARD, after_ts),
                )
                self._conn.commit()
                return cur.rowcount or 0
        except Exception:  # noqa: BLE001
            logger.exception("persist: forgetting failed (non-fatal)")
            return 0

    # -- where one conversation ends and the next begins ---------------
    #
    # Nothing had closed a conversation since 13 August: the code that
    # did lived in the engine deleted that day, and
    # `_active_conversation_id` only ever get-or-creates. So one row has
    # been open for nine days and holds every turn since — which makes
    # "what did we talk about" a question with exactly one answer, and
    # `summary` a field with nowhere to be written.
    #
    # A voice assistant has no hang-up, so the boundary can only be
    # silence. Long enough that a pause to think, or to go and look at
    # something, does not end anything.

    def close_idle_conversation(
        self, *, now: float, after_s: float
    ) -> tuple[int, list[tuple[int, str]]] | None:
        """End the open conversation if it has gone quiet, and hand back
        what was said in it.

        `end_ts` is the last thing said, not the moment this noticed.
        Stamping "now" would put the end of the conversation at whatever
        time a tick happened to run and make every gap look like part of
        it.

        Returns None when there is nothing open, when it is still warm,
        or when it holds nothing worth summarising.
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, start_ts FROM conversations WHERE end_ts IS NULL "
                "ORDER BY start_ts DESC LIMIT 1"
            )
            row = cur.fetchone()
            if row is None:
                return None
            conversation_id, start_ts = int(row[0]), float(row[1])

            cur = self._conn.execute(
                "SELECT MAX(t.ts) FROM transcripts t JOIN turns tu "
                "ON t.turn_id = tu.id WHERE tu.conversation_id = ?",
                (conversation_id,),
            )
            last_ts = (cur.fetchone() or [None])[0]
            # An empty conversation is not stale, it is unused. Closing
            # it would leave a row with no turns and open another exactly
            # like it on the next word.
            if last_ts is None:
                return None
            if now - float(last_ts) < after_s:
                return None

            cur = self._conn.execute(
                "SELECT t.agent_spoken, t.text FROM transcripts t JOIN turns tu "
                "ON t.turn_id = tu.id WHERE tu.conversation_id = ? "
                "ORDER BY t.ts ASC",
                (conversation_id,),
            )
            said = [(int(a), str(text)) for a, text in cur.fetchall() if text]

            self._conn.execute(
                "UPDATE conversations SET end_ts = ? WHERE id = ?",
                (float(last_ts), conversation_id),
            )
            self._conn.commit()
            logger.info(
                "persist: conversation %d closed — %d lines over %d min",
                conversation_id,
                len(said),
                int(max(0.0, float(last_ts) - start_ts) / 60),
            )
            return conversation_id, said

    def save_summary(self, conversation_id: int, summary: str) -> None:
        """Two sentences about what it was. Written once, never over a
        summary that is already there."""
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET summary = ? "
                "WHERE id = ? AND (summary IS NULL OR summary = '')",
                (summary.strip(), conversation_id),
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

    # -- role sessions -------------------------------------------------
    #
    # A role session is a meeting: it starts on a voice trigger and ends
    # on «закінчили», possibly an hour later. Held only in RoleManager's
    # memory it does not survive a daemon restart — the turns stay in the
    # DB as orphans and no artifact is ever built. These five methods are
    # the on-disk mirror of that session, so a restart can at least see
    # that a meeting was cut off instead of losing it in silence.

    def open_role_session(self, role_name: str, channel: str = "") -> int:
        """Record a session as live (ended_ts NULL). Returns its id."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO role_sessions "
                "(role_name, channel, started_ts, ended_ts, artifact_path, "
                " turn_ids, status) VALUES (?, ?, ?, NULL, NULL, '[]', 'live')",
                (role_name, channel or "", time.time()),
            )
            session_id = cur.lastrowid
            assert session_id is not None
            self._conn.commit()
            return session_id

    def note_role_turn(self, session_id: int, turn_id: int | None) -> None:
        """Append a persisted turn id to the session's JSON array.

        Read-modify-write rather than a child table: the array is the whole
        membership list, it is written once per turn (never per audio
        frame), and it is what makes a row self-contained for recovery.
        Duplicates are dropped so a retried write cannot double-count.
        """
        if turn_id is None:
            return
        with self._lock:
            cur = self._conn.execute(
                "SELECT turn_ids FROM role_sessions WHERE id = ?", (session_id,)
            )
            row = cur.fetchone()
            if row is None:
                return
            turn_ids = _decode_turn_ids(row[0])
            if turn_id in turn_ids:
                return
            turn_ids.append(int(turn_id))
            self._conn.execute(
                "UPDATE role_sessions SET turn_ids = ? WHERE id = ?",
                (json.dumps(turn_ids), session_id),
            )
            self._conn.commit()

    def close_role_session(
        self,
        session_id: int,
        artifact_path: str | None = None,
        status: str = "done",
    ) -> None:
        """Stamp ended_ts (so the session stops being live), the artifact
        path if one was written, and the final status."""
        with self._lock:
            self._conn.execute(
                "UPDATE role_sessions SET ended_ts = ?, artifact_path = ?, "
                "status = ? WHERE id = ?",
                (time.time(), artifact_path or None, status, session_id),
            )
            self._conn.commit()

    def set_role_session_artifact(self, session_id: int, artifact_path: str) -> None:
        """Attach the artifact file to an already-closed session.

        The path is only known after the caller has written the file, which
        happens after finish() has closed the row."""
        if not artifact_path:
            return
        with self._lock:
            self._conn.execute(
                "UPDATE role_sessions SET artifact_path = ? WHERE id = ?",
                (artifact_path, session_id),
            )
            self._conn.commit()

    def live_role_session(self) -> dict | None:
        """The session left open by a previous run, or None.

        Newest first: only one session can be live at a time, but a crash
        during close() could in principle leave two, and the most recent
        is the meeting the user was actually in.
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, role_name, channel, started_ts, ended_ts, "
                "artifact_path, turn_ids, status FROM role_sessions "
                "WHERE ended_ts IS NULL ORDER BY id DESC LIMIT 1"
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {
                "id": int(row[0]),
                "role_name": row[1],
                "channel": row[2],
                "started_ts": row[3],
                "ended_ts": row[4],
                "artifact_path": row[5],
                "turn_ids": _decode_turn_ids(row[6]),
                "status": row[7],
            }

    def role_session_turns(self, session_id: int) -> list[dict]:
        """The session's exchanges, oldest first — the material an
        artifact is built from. Same {"user", "agent"} shape as
        recent_exchanges, plus the turn id, and in the order the session
        recorded them (not the order the DB happens to return)."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT turn_ids FROM role_sessions WHERE id = ?", (session_id,)
            )
            row = cur.fetchone()
            if row is None:
                return []
            turn_ids = _decode_turn_ids(row[0])

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
                exchanges.append(
                    {"turn_id": turn_id, "user": user_text, "agent": agent_text}
                )
            return exchanges

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _decode_turn_ids(raw: str | None) -> list[int]:
    """Never let one malformed row cost a whole meeting."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [int(v) for v in value if isinstance(v, (int, float))]
