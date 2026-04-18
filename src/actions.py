"""Intent queue + async action worker for Phase 2.1.

Generator emits intents → IntentQueue buffers them → ActionWorker pops
FIFO and dispatches via ClaudeBackend.call_action. Conversation loop
never blocks on action execution.

The `HEARE_ACTION_TIMEOUT_SECONDS` and `HEARE_FAKE_CLAUDE_SLEEP` env vars
are test-only overrides — consumed by main.py and live-failure tests.
Production users should never set them.
"""
from __future__ import annotations

import asyncio
import collections
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .claude_backend_common import ClaudeBackend

logger = logging.getLogger("heare.actions")


@dataclass
class Intent:
    """One submitted intent awaiting (or in) execution."""
    id: int
    tool: str
    args: str
    raw: dict
    submitted_at: float = field(default_factory=time.time)


class IntentQueue:
    """FIFO async queue with tail-cancel and bounded size."""

    def __init__(self, max_pending: int = 32) -> None:
        self.max_pending = max_pending
        self._deque: collections.deque[Intent] = collections.deque()
        self._event = asyncio.Event()
        self._next_id: int = 1

    async def submit(self, payload: dict) -> int | None:
        tool = str(payload.get("tool", "")).strip()
        args = str(payload.get("args", "")).strip()
        if not tool:
            logger.warning("rejecting intent with empty tool: %r", payload)
            return None
        if len(self._deque) >= self.max_pending:
            logger.warning(
                "[INTENT DROPPED — queue full max=%d] tool=%s",
                self.max_pending,
                tool,
            )
            return None
        intent = Intent(id=self._next_id, tool=tool, args=args, raw=payload)
        self._next_id += 1
        self._deque.append(intent)
        self._event.set()
        return intent.id

    async def next(self) -> Intent:
        while not self._deque:
            self._event.clear()
            await self._event.wait()
        intent = self._deque.popleft()
        if not self._deque:
            self._event.clear()
        return intent

    def cancel_latest(self) -> Intent | None:
        if not self._deque:
            return None
        intent = self._deque.pop()
        if not self._deque:
            self._event.clear()
        return intent

    def pending_count(self) -> int:
        return len(self._deque)


OnResult = Callable[[Intent, str], Awaitable[None]]
OnError = Callable[[Intent, BaseException], Awaitable[None]]


class ActionWorker:
    """Pulls intents off the queue and dispatches them via Claude CLI.

    Dispatch contract (PRD US-P2.1-04):
        description = f"Use the {intent.tool} tool: {intent.args}"
        result = await claude_cli.call_action(description)
        → await on_result(intent, result["summary"])
    """

    def __init__(
        self,
        queue: IntentQueue,
        claude_cli: "ClaudeBackend",
        on_result: OnResult,
        on_error: OnError,
        timeout: float = 120.0,
    ) -> None:
        self.queue = queue
        self.claude_cli = claude_cli
        self.on_result = on_result
        self.on_error = on_error
        self.timeout = timeout

    async def run(self) -> None:
        while True:
            intent = await self.queue.next()
            await self._process_one(intent)

    async def _process_one(self, intent: Intent) -> None:
        description = f"Use the {intent.tool} tool: {intent.args}"
        call_task = asyncio.create_task(self.claude_cli.call_action(description))
        try:
            result = await asyncio.wait_for(
                asyncio.shield(call_task), timeout=self.timeout
            )
        except asyncio.TimeoutError as exc:
            call_task.cancel()
            killed = False
            try:
                kill = getattr(self.claude_cli, "kill_running_action", None)
                if kill is not None:
                    killed = bool(await kill())
            except Exception:
                logger.exception("kill_running_action raised; treating as not killed")
            logger.warning(
                "[ACTION TIMEOUT id=%d killed_subprocess=%s]", intent.id, killed
            )
            await self._safe_call_error(intent, exc)
            return
        except asyncio.CancelledError:
            call_task.cancel()
            raise
        except BaseException as exc:
            await self._safe_call_error(intent, exc)
            return

        summary = result.get("summary", "") if isinstance(result, dict) else str(result)
        await self._safe_call_result(intent, summary)

    async def _safe_call_result(self, intent: Intent, summary: str) -> None:
        try:
            await self.on_result(intent, summary)
        except Exception:
            logger.exception("on_result raised for intent id=%d (swallowed)", intent.id)

    async def _safe_call_error(self, intent: Intent, exc: BaseException) -> None:
        try:
            await self.on_error(intent, exc)
        except Exception:
            logger.exception("on_error raised for intent id=%d (swallowed)", intent.id)
