"""SQLite transcript + decision store via aiosqlite so the audio pipeline never blocks on disk."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

import aiosqlite


SCHEMA_VERSION = 8

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
    agent_mode TEXT,
    agent_spoken INTEGER,
    turn_id INTEGER REFERENCES turns(id)
);

CREATE TABLE IF NOT EXISTS displays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    title TEXT,
    format TEXT NOT NULL,
    content_type TEXT,
    content TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    decision_id INTEGER,
    status TEXT NOT NULL,
    result_summary TEXT,
    tool TEXT,
    args TEXT,
    result_json TEXT,
    intent_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_transcripts_ts ON transcripts(ts DESC);
CREATE INDEX IF NOT EXISTS idx_actions_ts ON actions(ts DESC);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_ts REAL NOT NULL,
    end_ts REAL,
    mode TEXT NOT NULL,
    summary TEXT,
    entity_map TEXT
);

CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    start_ts REAL NOT NULL,
    end_ts REAL NOT NULL,
    aggregated_text TEXT NOT NULL,
    utterance_count INTEGER NOT NULL,
    topic_tags TEXT,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS dynamic_tools (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    sdk_name TEXT NOT NULL,
    execution_type TEXT NOT NULL,
    description TEXT NOT NULL,
    enabled BOOLEAN DEFAULT TRUE,
    definition_json TEXT NOT NULL,
    created_ts REAL NOT NULL,
    modified_ts REAL NOT NULL,
    last_used_ts REAL,
    usage_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_conversations_mode ON conversations(mode);
CREATE INDEX IF NOT EXISTS idx_conversations_active ON conversations(end_ts) WHERE end_ts IS NULL;
CREATE INDEX IF NOT EXISTS idx_turns_conversation ON turns(conversation_id);
CREATE INDEX IF NOT EXISTS idx_dynamic_tools_enabled ON dynamic_tools(enabled);
CREATE INDEX IF NOT EXISTS idx_dynamic_tools_last_used ON dynamic_tools(last_used_ts DESC);

CREATE TABLE IF NOT EXISTS user_profile (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

-- USE-001: append-only ledger of every paid API call so the dashboard
-- can show running token / cost totals. ``kind`` discriminates the
-- type-specific columns: 'llm' uses input/output_tokens; 'stt' uses
-- audio_seconds; 'tts' uses char_count. ``cost_usd`` is precomputed
-- via src/pricing.py at insert time so reads stay aggregate-only and
-- a future price-table change doesn't retroactively rewrite history.
CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    audio_seconds REAL,
    char_count INTEGER,
    cost_usd REAL
);

CREATE INDEX IF NOT EXISTS idx_usage_events_ts ON usage_events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_usage_events_kind ON usage_events(kind);
"""


class TranscriptStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        # WAL mode: watch refresh is 0.5s, reader/writer would otherwise
        # contend. Idempotent on already-WAL DBs.
        await self._db.execute("PRAGMA journal_mode=WAL")
        # Foreign keys are off by default in SQLite; without this the
        # ON DELETE SET NULL cascade on events.decision_id never fires.
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        await self._migrate_action_log_columns()
        await self._migrate_displays_content_type()
        await self._check_schema_version()

    async def _migrate_action_log_columns(self) -> None:
        # CCS-01: persist the in-memory action log to the actions table.
        # Idempotent ALTERs — old rows surface tool/args/result_json/intent_id
        # as NULL and are filtered out at hydrate time.
        for col_ddl in (
            "ALTER TABLE actions ADD COLUMN tool TEXT",
            "ALTER TABLE actions ADD COLUMN args TEXT",
            "ALTER TABLE actions ADD COLUMN result_json TEXT",
            "ALTER TABLE actions ADD COLUMN intent_id INTEGER",
        ):
            try:
                await self.db.execute(col_ddl)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise

        # CCS-01: action_log entries are keyed by intent_id, NOT decision_id —
        # the original schema declared decision_id NOT NULL which blocks rows
        # that come from the action log alone. Detect the constraint and
        # rebuild the table without it. Idempotent: noop if already nullable.
        cursor = await self.db.execute("PRAGMA table_info(actions)")
        cols = await cursor.fetchall()
        decision_notnull = any(row[1] == "decision_id" and row[3] == 1 for row in cols)
        if decision_notnull:
            # SQLite has no DROP NOT NULL; recreate the table preserving
            # data + indices. PRAGMA foreign_keys must be off across the
            # rebuild (per SQLite docs §7.11). Wrap the lot in a single tx.
            await self.db.execute("PRAGMA foreign_keys=OFF")
            await self.db.execute("BEGIN")
            try:
                await self.db.execute(
                    """
                    CREATE TABLE actions_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts REAL NOT NULL,
                        decision_id INTEGER,
                        status TEXT NOT NULL,
                        result_summary TEXT,
                        tool TEXT,
                        args TEXT,
                        result_json TEXT,
                        intent_id INTEGER
                    )
                    """
                )
                await self.db.execute(
                    """
                    INSERT INTO actions_new
                        (id, ts, decision_id, status, result_summary,
                         tool, args, result_json, intent_id)
                    SELECT id, ts, decision_id, status, result_summary,
                           tool, args, result_json, intent_id
                    FROM actions
                    """
                )
                await self.db.execute("DROP TABLE actions")
                await self.db.execute("ALTER TABLE actions_new RENAME TO actions")
                await self.db.execute("COMMIT")
            except Exception:
                await self.db.execute("ROLLBACK")
                await self.db.execute("PRAGMA foreign_keys=ON")
                raise
            await self.db.execute("PRAGMA foreign_keys=ON")

        # UNIQUE index on intent_id is required for ON CONFLICT(intent_id).
        # SQLite treats NULLs as distinct in UNIQUE indexes, so legacy rows
        # (log_action: decision_id-only, intent_id IS NULL) coexist freely
        # without clashing with each other or the action_log entries.
        # NOTE: ON CONFLICT does NOT work with partial indices, so this is
        # an unconditional UNIQUE index.
        await self.db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_actions_intent_id "
            "ON actions(intent_id)"
        )
        await self.db.commit()

    async def _migrate_displays_content_type(self) -> None:
        try:
            await self.db.execute("ALTER TABLE displays ADD COLUMN content_type TEXT")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise

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
        agent_mode: str | None = None,
        agent_spoken: bool | None = None,
    ) -> int:
        now = time.time()
        # Deduplication: ignore identical transcript text within 2 seconds.
        # Prevents duplicates when Groq STT sends multiple TranscriptionFrame
        # objects for the same utterance.
        cursor = await self.db.execute(
            "SELECT id FROM transcripts WHERE text = ? AND ts > ? ORDER BY ts DESC LIMIT 1",
            (text, now - 2.0),
        )
        row = await cursor.fetchone()
        if row is not None:
            logger.debug("dedup: ignoring duplicate transcript %r", text[:40])
            return row[0]

        cursor = await self.db.execute(
            "INSERT INTO transcripts (ts, text, mode, agent_mode, agent_spoken)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                now,
                text,
                mode,
                agent_mode,
                None if agent_spoken is None else int(agent_spoken),
            ),
        )
        await self.db.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def latest_bot_response(self) -> dict[str, Any] | None:
        """Most recent agent text response (mode='assistant'), or None.

        This is the read side of the text-response channel: the assistant
        response logger persists every LLM answer here regardless of
        whether TTS spoke it, so any consumer (watch dashboard, future
        Telegram/web) can show what the agent said/would say without
        coupling to the speech path.
        """
        cursor = await self.db.execute(
            "SELECT ts, text, agent_mode, agent_spoken FROM transcripts"
            " WHERE mode = 'assistant' ORDER BY ts DESC LIMIT 1",
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "ts": row[0],
            "text": row[1],
            "agent_mode": row[2],
            "agent_spoken": None if row[3] is None else bool(row[3]),
        }

    async def log_display(
        self, content: str, fmt: str, title: str | None = None
    ) -> int:
        """Persist a rich display block (the latest-only display channel).

        The agent calls the ``show_display`` tool to surface long /
        code / ASCII / structured output the dashboard renders visually
        instead of speaking it. Any consumer reads the newest row.
        """
        now = time.time()
        cursor = await self.db.execute(
            "INSERT INTO displays (ts, title, format, content) VALUES (?, ?, ?, ?)",
            (now, title, fmt, content),
        )
        await self.db.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def latest_display(self) -> dict[str, Any] | None:
        """Most recent display block, or None."""
        cursor = await self.db.execute(
            "SELECT ts, title, format, content_type, content FROM displays"
            " ORDER BY ts DESC LIMIT 1",
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "ts": row[0],
            "title": row[1],
            "format": row[2],
            "content_type": row[3],
            "content": row[4],
        }

    async def upsert_action_log_entry(
        self,
        *,
        intent_id: int,
        tool: str,
        args: str,
        status: str,
        result: str | None,
        ts: float | None = None,
    ) -> int:
        """Insert or update an action_log row keyed by intent_id.

        CCS-01: persists the in-memory action log to SQLite so the deque
        can be rehydrated on daemon restart. ON CONFLICT(intent_id) keeps
        a single row per intent across pending → done/error transitions.
        ``result`` is the raw JSON string for ``result_json``; pass None
        for the pending state.
        """
        when = ts if ts is not None else time.time()
        cursor = await self.db.execute(
            """
            INSERT INTO actions (ts, status, tool, args, result_json, intent_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(intent_id) DO UPDATE SET
                ts = excluded.ts,
                status = excluded.status,
                tool = excluded.tool,
                args = excluded.args,
                result_json = excluded.result_json
            """,
            (when, status, tool, args, result, intent_id),
        )
        await self.db.commit()
        # cursor.lastrowid is undefined on UPDATE in SQLite; resolve from intent_id.
        if cursor.lastrowid:
            return cursor.lastrowid
        sel = await self.db.execute(
            "SELECT id FROM actions WHERE intent_id = ?", (intent_id,)
        )
        row = await sel.fetchone()
        assert row is not None
        return int(row[0])

    async def load_recent_action_log(
        self,
        *,
        limit: int = 16,
        since_ts: float | None = None,
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` newest action_log rows (intent_id IS NOT NULL),
        filtered to ts >= since_ts when provided. Order newest-first.

        Legacy rows (decision_id-only, intent_id IS NULL) are excluded.
        """
        if since_ts is None:
            cursor = await self.db.execute(
                """
                SELECT intent_id, ts, tool, args, status, result_json
                FROM actions
                WHERE intent_id IS NOT NULL
                ORDER BY ts DESC
                LIMIT ?
                """,
                (limit,),
            )
        else:
            cursor = await self.db.execute(
                """
                SELECT intent_id, ts, tool, args, status, result_json
                FROM actions
                WHERE intent_id IS NOT NULL AND ts >= ?
                ORDER BY ts DESC
                LIMIT ?
                """,
                (since_ts, limit),
            )
        rows = await cursor.fetchall()
        return [
            {
                "intent_id": r[0],
                "ts": r[1],
                "tool": r[2],
                "args": r[3],
                "status": r[4],
                "result_json": r[5],
            }
            for r in rows
        ]

    async def recent_transcripts(self, n: int = 5) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            "SELECT id, ts, text, mode FROM transcripts ORDER BY ts DESC LIMIT ?",
            (n,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "ts": r[1],
                "text": r[2],
                "mode": r[3],
            }
            for r in reversed(rows)
        ]

    # Conversation memory methods

    async def start_conversation(self, mode: str) -> int:
        """Start a new conversation session."""
        cursor = await self.db.execute(
            "INSERT INTO conversations (start_ts, mode) VALUES (?, ?)",
            (time.time(), mode),
        )
        await self.db.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def end_conversation(self, conversation_id: int) -> None:
        """End a conversation session."""
        await self.db.execute(
            "UPDATE conversations SET end_ts = ? WHERE id = ? AND end_ts IS NULL",
            (time.time(), conversation_id),
        )
        await self.db.commit()

    async def get_active_conversation(self) -> dict[str, Any] | None:
        """Get the currently active conversation (end_ts IS NULL)."""
        cursor = await self.db.execute(
            """
            SELECT id, start_ts, end_ts, mode, summary, entity_map
            FROM conversations
            WHERE end_ts IS NULL
            ORDER BY start_ts DESC
            LIMIT 1
            """
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "start_ts": row[1],
            "end_ts": row[2],
            "mode": row[3],
            "summary": row[4],
            "entity_map": row[5],
        }

    async def get_recent_turns(
        self, conversation_id: int, n: int = 3
    ) -> list[dict[str, Any]]:
        """Get the last N turns for a conversation."""
        cursor = await self.db.execute(
            """
            SELECT id, start_ts, end_ts, aggregated_text, utterance_count, topic_tags
            FROM turns
            WHERE conversation_id = ?
            ORDER BY start_ts DESC
            LIMIT ?
            """,
            (conversation_id, n),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "start_ts": r[1],
                "end_ts": r[2],
                "aggregated_text": r[3],
                "utterance_count": r[4],
                "topic_tags": json.loads(r[5]) if r[5] else None,
            }
            for r in reversed(rows)
        ]

    # Dynamic tools methods

    async def create_dynamic_tool(
        self,
        *,
        name: str,
        sdk_name: str,
        execution_type: str,
        description: str,
        definition_json: str,
        enabled: bool = True,
    ) -> int:
        """Create a new dynamic tool."""
        now = time.time()
        cursor = await self.db.execute(
            """
            INSERT INTO dynamic_tools
                (name, sdk_name, execution_type, description, enabled, definition_json, created_ts, modified_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                sdk_name,
                execution_type,
                description,
                enabled,
                definition_json,
                now,
                now,
            ),
        )
        await self.db.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def update_dynamic_tool(
        self,
        name: str,
        *,
        sdk_name: str | None = None,
        execution_type: str | None = None,
        description: str | None = None,
        definition_json: str | None = None,
        enabled: bool | None = None,
    ) -> bool:
        """Update a dynamic tool."""
        updates: list[tuple[str, Any]] = []
        values: list[Any] = []

        if sdk_name is not None:
            updates.append("sdk_name = ?")
            values.append(sdk_name)
        if execution_type is not None:
            updates.append("execution_type = ?")
            values.append(execution_type)
        if description is not None:
            updates.append("description = ?")
            values.append(description)
        if definition_json is not None:
            updates.append("definition_json = ?")
            values.append(definition_json)
        if enabled is not None:
            updates.append("enabled = ?")
            values.append(enabled)

        if not updates:
            return False

        updates.append("modified_ts = ?")
        values.append(time.time())
        values.append(name)

        cursor = await self.db.execute(
            f"UPDATE dynamic_tools SET {', '.join(updates)} WHERE name = ?",
            values,
        )
        await self.db.commit()
        return (cursor.rowcount or 0) > 0

    async def delete_dynamic_tool(self, name: str) -> bool:
        """Delete a dynamic tool."""
        cursor = await self.db.execute(
            "DELETE FROM dynamic_tools WHERE name = ?",
            (name,),
        )
        await self.db.commit()
        return (cursor.rowcount or 0) > 0

    async def load_all_dynamic_tools(self) -> list[dict[str, Any]]:
        """Load all enabled dynamic tools for startup hydration."""
        cursor = await self.db.execute(
            """
            SELECT id, name, sdk_name, execution_type, description, enabled, definition_json,
                   created_ts, modified_ts, last_used_ts, usage_count
            FROM dynamic_tools
            WHERE enabled = 1
            ORDER BY name
            """
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "sdk_name": r[2],
                "execution_type": r[3],
                "description": r[4],
                "enabled": bool(r[5]),
                "definition_json": r[6],
                "created_ts": r[7],
                "modified_ts": r[8],
                "last_used_ts": r[9],
                "usage_count": r[10],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # USE-001: usage_events — append-only ledger of paid API calls.

    async def record_usage_event(
        self,
        *,
        kind: str,
        provider: str | None = None,
        model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        audio_seconds: float | None = None,
        char_count: int | None = None,
        cost_usd: float | None = None,
        ts: float | None = None,
    ) -> None:
        """Append one paid-API event to ``usage_events``.

        ``kind`` is one of ``'llm'``, ``'stt'``, ``'tts'`` — discriminates
        which type-specific columns are populated. ``cost_usd`` is
        precomputed by the caller via :mod:`src.agent.llm.pricing`; we store the
        snapshot rather than the inputs so a future price-table change
        doesn't retroactively rewrite the ledger.
        """
        await self.db.execute(
            """
            INSERT INTO usage_events
                (ts, kind, provider, model,
                 input_tokens, output_tokens, audio_seconds, char_count, cost_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts if ts is not None else time.time(),
                kind,
                provider,
                model,
                input_tokens,
                output_tokens,
                audio_seconds,
                char_count,
                cost_usd,
            ),
        )
        await self.db.commit()
