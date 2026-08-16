"""VoiceToolbox — the voice agent's three verbs: delegate, remember, recall.

Mirrors the daemon's voice/hands split (see ``src/agent/hands.py`` and the
``VOICE_TOOLS`` set in ``src/agent/tools/system.py``): the voice model,
under a latency budget, only ever chooses among three schemas. Everything
heavier — files, the shell, the web — goes to ``Hands``, which has no
deadline and is not in the speaking path.

This module deliberately does not import ``src.agent.tools.system`` (it
drags in Pipecat and the rest of the sixty-tool surface). The schema
SHAPES below are copied in spirit, not by reference.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from src.agent.hands import PROGRESS_AFTER, Hands
from src.memory.base import MemoryEntry, MemoryType

logger = logging.getLogger("heare.spine.tools")

# A constructor seam: tests inject a fake in place of a real Hands
# instance without touching this module's import graph. Signature matches
# ``Hands.__init__``'s one required positional argument.
HandsFactory = Callable[[Any], Any]


def _default_hands_factory(settings: Any) -> Any:
    return Hands(settings)


# An MCP call crosses a pipe to another process; a server that never
# answers must not hold a job open forever. Sits above the built-in
# default (30s) because "npx some-server" doing real work is normal.
MCP_CALL_TIMEOUT_S = 60.0

# How long a job stays quiet before the spine says "still working". Same
# number as the pipecat path (``hands.PROGRESS_AFTER``) — a module-level
# name here so it is one constant, readable at call time.
BEACON_INTERVAL_S = PROGRESS_AFTER

# How many tool names to keep in memory so a result row can still say
# which tool produced it. One turn's worth, many times over.
_ACTION_TRAIL_MAX = 64


class McpHands(Hands):
    """The worker, plus the ability to call MCP tools.

    ``Hands`` dispatches every tool through ``execute_direct``, which
    knows the built-ins and nothing else — an ``mcp__*`` name reaches it
    as "Unknown direct tool". The tools themselves arrive on their own:
    the bridge appends them to the module-level ToolDef list that
    ``Hands._tool_schemas`` reads, so the worker already *sees* them.
    This subclass is only the other half — being able to *run* them —
    and it lives here rather than in ``hands.py`` so the daemon's other
    engine is untouched.

    ``mcp_provider`` is a callable rather than a bridge because the
    worker is built while the loop is being wired and the servers are
    connected after that; the lookup has to happen at call time.

    ``on_long_running`` is the other half of the same problem in the
    other direction. ``Hands._beacon`` reaches for the indication facade,
    which only the pipecat build installs — on the spine it finds nothing
    and returns, so between «Прийнято, роблю.» and the answer there is
    silence of unknown length. Overriding ``_beacon`` here (it is called
    through ``self`` from ``Hands._run``) gives the spine a seam without
    touching ``hands.py``: whatever the composition root passes gets
    called with the job's label every ``BEACON_INTERVAL_S``.
    """

    def __init__(
        self,
        settings: Any,
        *,
        mcp_provider: Callable[[], Any] | None = None,
        on_long_running: Callable[[str], Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(settings, **kwargs)
        self._mcp_provider = mcp_provider
        self._on_long_running = on_long_running

    async def _beacon(self, label: str) -> None:
        """Say "still working" the spine's way, or fall back to the daemon's."""
        if self._on_long_running is None:
            # No seam wired: exactly today's behaviour — the indication
            # facade if one exists, silence if it does not.
            await super()._beacon(label)
            return
        while True:
            await asyncio.sleep(BEACON_INTERVAL_S)
            logger.info("[SPINE TOOLS] still working: %s", label)
            try:
                result = self._on_long_running(label)
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a beacon must not kill the job
                logger.exception("[SPINE TOOLS] long-running callback failed")

    async def _execute(self, name: str, arguments: dict) -> str:
        if not name.startswith("mcp__"):
            return await super()._execute(name, arguments)

        from src.agent.modes import mode_gate_refusal

        # Same gate as the built-in path, on the full mcp__slug__tool
        # name — the schema filter already dropped it, but a model can
        # still name a tool it was never shown.
        refusal = mode_gate_refusal(self._session_state, name)
        if refusal is not None:
            return f"{name} refused: {refusal.get('error', 'blocked by mode')}"

        bridge = None
        if self._mcp_provider is not None:
            try:
                bridge = self._mcp_provider()
            except Exception:  # noqa: BLE001
                logger.exception("[SPINE TOOLS] mcp provider failed")
        if bridge is None:
            return f"{name} failed: no MCP servers are connected"

        intent_id = self._record_pending(name, json.dumps(arguments, ensure_ascii=False))
        try:
            result = await asyncio.wait_for(
                bridge.call(name, arguments), timeout=MCP_CALL_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            self._record_error(intent_id, "timed out")
            return f"{name} timed out"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("[SPINE TOOLS] %s failed", name)
            self._record_error(intent_id, repr(exc))
            return f"{name} failed: {exc}"

        if isinstance(result, dict) and result.get("success"):
            output = str(result.get("output", ""))[:6000] or "done"
            self._record_result(intent_id, output)
            return output
        error = (
            str(result.get("error", "unknown error"))
            if isinstance(result, dict)
            else str(result)
        )
        self._record_error(intent_id, error)
        return f"{name} failed: {error}"


class SpineActionLog:
    """The daemon's ``actions`` rows, written from the spine.

    ``Hands`` records every tool call through a collaborator shaped like
    ``ConversationManager`` — ``record_action_pending / _result / _error``
    — and no-ops when it has none. The spine had none, so the actions
    table stayed empty and the dashboard's activity feed could show what
    was SAID but never what was DONE.

    A whole ``ConversationManager`` is the wrong size for that: it exists
    to maintain topics, entities, a summary and a rehydrated in-memory
    deque for the *pipeline's* prompt, all on a ``TranscriptStore`` the
    spine does not build. What ``Hands`` actually needs from it is three
    sync methods and one table. This is those three methods, writing the
    same rows (same columns, same statuses, same ``result_json`` shape as
    ``TranscriptStore.upsert_action_log_entry``) so one feed reads both
    engines.

    Writes are fire-and-forget tasks, as in ``ConversationManager``: the
    caller is inside a tool call and must not wait on disk. They are
    serialised by a lock, because a pending row and its result are two
    tasks and interleaving them would leave two rows for one intent.
    """

    def __init__(self, db: Any) -> None:
        """db: an aiosqlite-shaped connection (async execute/commit)."""
        self._db = db
        self._pending: set[asyncio.Task] = set()
        self._lock: asyncio.Lock | None = None
        # intent_id -> (tool, args). A result arrives without them, and a
        # row that cannot name its tool is not worth showing.
        self._trail: OrderedDict[int, tuple[str, str]] = OrderedDict()

    # -- the ConversationManager shape ---------------------------------

    def record_action_pending(self, intent_id: int, tool: str, args: str) -> None:
        self._trail[intent_id] = (tool, args)
        while len(self._trail) > _ACTION_TRAIL_MAX:
            self._trail.popitem(last=False)
        self._schedule(intent_id, "pending", None)

    def record_action_result(
        self, intent_id: int, summary: str, *, items: list[dict] | None = None
    ) -> None:
        payload: dict[str, Any] = {"summary": summary}
        if items is not None:
            payload["items"] = items
        self._schedule(intent_id, "done", json.dumps(payload, ensure_ascii=False))

    def record_action_error(self, intent_id: int, error: str) -> None:
        self._schedule(
            intent_id, "error", json.dumps({"error": error}, ensure_ascii=False)
        )

    def record_action_cancelled(
        self, intent_id: int, tool: str = "", args: str = ""
    ) -> None:
        if tool or args:
            self._trail.setdefault(intent_id, (tool, args))
        self._schedule(intent_id, "cancelled", None)

    # -- the writer ----------------------------------------------------

    def _schedule(self, intent_id: int, status: str, result: str | None) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (a sync test path) — nothing to write onto.
            return
        tool, args = self._trail.get(intent_id, ("", ""))
        task = loop.create_task(
            self._write(intent_id, tool, args, status, result, time.time())
        )
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _write(
        self,
        intent_id: int,
        tool: str,
        args: str,
        status: str,
        result: str | None,
        ts: float,
    ) -> None:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            try:
                # Not ON CONFLICT: the UNIQUE index on actions(intent_id)
                # is created by the daemon's async migrations, and a DB
                # the spine opened on its own has never run them.
                cursor = await self._db.execute(
                    "SELECT id FROM actions WHERE intent_id = ?", (intent_id,)
                )
                row = await cursor.fetchone()
                if row is None:
                    await self._db.execute(
                        "INSERT INTO actions "
                        "(ts, status, tool, args, result_json, intent_id) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (ts, status, tool, args, result, intent_id),
                    )
                else:
                    await self._db.execute(
                        "UPDATE actions SET ts = ?, status = ?, tool = ?, "
                        "args = ?, result_json = ? WHERE intent_id = ?",
                        (ts, status, tool, args, result, intent_id),
                    )
                await self._db.commit()
            except Exception as exc:  # noqa: BLE001 — never into the caller
                logger.warning(
                    "actions: write failed intent_id=%s status=%s: %s",
                    intent_id,
                    status,
                    exc,
                )

    async def flush(self) -> None:
        """Wait for the scheduled writes. For shutdown and for tests."""
        for _ in range(8):
            pending = [t for t in self._pending if not t.done()]
            if not pending:
                return
            await asyncio.gather(*pending, return_exceptions=True)


@dataclass
class SpineRecords:
    """What the spine remembers about work it did: jobs and actions."""

    jobs: Any = None
    actions: SpineActionLog | None = None
    stranded: list = field(default_factory=list)
    db: Any = None

    async def close(self) -> None:
        if self.actions is not None:
            await self.actions.flush()
        if self.db is not None:
            try:
                await self.db.close()
            except Exception:  # noqa: BLE001
                logger.debug("records: close failed (non-fatal)", exc_info=True)


async def open_spine_records(db_path: str | Path) -> SpineRecords:
    """Open the two tables delegated work leaves behind.

    One connection to the same ``~/.heare/heare.db`` the daemon uses:
    ``jobs`` (through the real ``JobStore``, so the SQL is not written
    twice) and ``actions`` (through ``SpineActionLog``). Sweeping
    interrupted jobs happens here because a job still marked ``running``
    at startup can only have been cut off by the last process ending.

    Never raises: a spine that cannot write the record still talks.
    """
    from src.agent.jobs import JobStore

    records = SpineRecords()
    try:
        import aiosqlite

        from src.store.storage import SCHEMA

        db = await aiosqlite.connect(str(db_path))
        await db.execute("PRAGMA journal_mode=WAL")
        # Idempotent, and the same script SpinePersistence runs — the
        # actions table has to exist before anything writes to it, and
        # the spine may reach here before the daemon ever has.
        await db.executescript(SCHEMA)
        await db.commit()
        records.db = db
        jobs = JobStore(db)
        await jobs.init()
        records.jobs = jobs
        records.stranded = await jobs.sweep_interrupted()
        for job in records.stranded:
            logger.info("jobs: interrupted by restart — %.120s", job.task)
        records.actions = SpineActionLog(db)
    except Exception:  # noqa: BLE001
        logger.exception("records: unavailable (non-fatal)")
    return records


def make_hands_factory(
    *,
    session_state: Any = None,
    mcp_provider: Callable[[], Any] | None = None,
    jobs: Any = None,
    conversation_manager: Any = None,
    on_long_running: Callable[[str], Any] | None = None,
) -> HandsFactory:
    """The worker the spine builds: mode-gated, MCP-capable, and — when
    the composition root passes them — remembering.

    Every collaborator is optional and defaults to None, which is exactly
    what the worker was built with before: no jobs table, no action log,
    no progress beacon. Nothing breaks while the composition root catches
    up; each one that gets passed turns a silence into a record.
    """

    def _factory(settings: Any) -> Any:
        return McpHands(
            settings,
            session_state=session_state,
            mcp_provider=mcp_provider,
            jobs=jobs,
            conversation_manager=conversation_manager,
            on_long_running=on_long_running,
        )

    return _factory


SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "delegate",
            "description": (
                "Hand a task to your worker: anything needing files, the "
                "shell, the web, settings, the browser, or more than a "
                "moment's thought. Returns at once — say in one short "
                "sentence that you are on it, then stop. The answer "
                "arrives later as its own message."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "What to do, in full. The worker cannot see "
                            "this conversation, so name everything it "
                            "needs."
                        ),
                    }
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Store a fact in persistent memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["fact", "preference", "decision", "event"],
                        "description": "Type of memory to store.",
                    },
                    "content": {
                        "type": "string",
                        "description": "What to remember. A short sentence.",
                    },
                },
                "required": ["type", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "Search your persistent memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for in your memories.",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


class VoiceToolbox:
    """The voice agent's three verbs. Anything heavier goes to Hands."""

    def __init__(
        self,
        settings: Any,
        memory: Any,
        deliver: Callable[[str], Awaitable[None]],
        *,
        hands_factory: HandsFactory | None = None,
    ) -> None:
        """memory: an initialized SQLiteBackend (or compatible).
        deliver: async fn; Hands results are delivered through it
        (wired here via ``hands.set_delivery(deliver)``).

        hands_factory: optional seam for tests — a callable taking
        ``settings`` and returning a Hands-compatible object (must expose
        ``set_delivery``, ``start``, ``cancel_all``). Defaults to building
        a real ``Hands(settings)``. Hands is instantiated here, lazily,
        rather than at import time, so importing this module stays cheap.
        """
        self._settings = settings
        self._memory = memory
        self._deliver = deliver
        factory = hands_factory or _default_hands_factory
        self._hands = factory(settings)
        self._hands.set_delivery(deliver)

    @property
    def schemas(self) -> list[dict]:
        """OpenAI function-calling schemas for delegate / remember / recall."""
        return SCHEMAS

    async def execute(self, name: str, arguments: dict) -> str:
        """Run one tool. Returns a SHORT Ukrainian sentence for the voice
        model to speak. Never raises into the voice loop."""
        try:
            if name == "delegate":
                return self._delegate(arguments)
            if name == "remember":
                return await self._remember(arguments)
            if name == "recall":
                return await self._recall(arguments)
            return "Такої дії я не знаю."
        except Exception:
            logger.exception("[SPINE TOOLS] %s failed: %.200s", name, arguments)
            return "Не вийшло. Спробуй ще раз."

    def _delegate(self, arguments: dict) -> str:
        task = str(arguments.get("task", "")).strip()
        if not task:
            return "Скажи, що саме зробити."
        # Never awaited: start() returns immediately, the result re-enters
        # the conversation later through the wired ``deliver`` callback.
        self._hands.start(task)
        return "Прийнято, роблю."

    async def _remember(self, arguments: dict) -> str:
        content = str(arguments.get("content", "")).strip()
        if not content:
            return "Що саме запам'ятати?"
        type_str = str(arguments.get("type", "")).strip() or "fact"
        try:
            memory_type = MemoryType(type_str)
        except ValueError:
            memory_type = MemoryType.FACT
        entry = MemoryEntry(id="", type=memory_type, content=content)
        await self._memory.store(entry)
        return "Запам'ятав."

    async def _recall(self, arguments: dict) -> str:
        query = str(arguments.get("query", "")).strip()
        if not query:
            return "Що пошукати?"
        results = await self._memory.search(query, limit=3)
        if not results:
            return "Нічого не знайшов."
        facts = [str(getattr(r, "content", r)).strip() for r in results[:3]]
        facts = [f for f in facts if f]
        if not facts:
            return "Нічого не знайшов."
        return " ".join(facts)

    def cancel_all(self) -> int:
        """Stop everything Hands has in flight."""
        return self._hands.cancel_all()
