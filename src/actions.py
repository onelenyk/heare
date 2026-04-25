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
from .direct_tools import execute_direct, _is_simple_tool
from .workflow import WorkflowStore, execute_workflow as run_workflow
from .tool_registry import is_tool_allowed, INTENT_TOOL_TO_SDK, get_enabled_tools

logger = logging.getLogger("heare.actions")
MAX_ARGS_LEN: int = 2000


@dataclass
class Intent:
    """One submitted intent awaiting (or in) execution."""
    id: int
    tool: str
    args: str
    raw: dict
    submitted_at: float = field(default_factory=time.time)
    # Phase B-0: correlation handles so the action worker can update the
    # right rows in decisions / actions. None is legal (e.g. Generator ran
    # without a store, or the legacy code path) — callbacks must tolerate it.
    decision_id: int | None = None
    transcript_id: int | None = None
    language: str = "en"


class IntentQueue:
    """FIFO async queue with tail-cancel and bounded size."""

    def __init__(self, max_pending: int = 32) -> None:
        self.max_pending = max_pending
        self._deque: collections.deque[Intent] = collections.deque()
        self._event = asyncio.Event()
        self._next_id: int = 1

    async def submit(
        self,
        payload: dict,
        *,
        decision_id: int | None = None,
        transcript_id: int | None = None,
        language: str = "en",
    ) -> int | None:
        tool = str(payload.get("tool", "")).strip()
        args = str(payload.get("args", "")).strip()
        if not tool:
            logger.warning("rejecting intent with empty tool: %r", payload)
            return None
        from .indication import IndicationKind, get_indication

        ind = get_indication()
        if not is_tool_allowed(tool):
            logger.warning(
                "[INTENT REJECTED — tool not allowed tool=%s allowed=%s]",
                tool,
                sorted(get_enabled_tools()),
            )
            if ind is not None:
                ind.notify(
                    IndicationKind.ACTION_REJECTED,
                    body=f"tool not allowed: {tool}",
                )
            return None
        if len(args) > MAX_ARGS_LEN:
            logger.warning(
                "[INTENT REJECTED — args too long len=%d max=%d tool=%s]",
                len(args),
                MAX_ARGS_LEN,
                tool,
            )
            if ind is not None:
                ind.notify(
                    IndicationKind.ACTION_REJECTED,
                    body=f"args too long: {tool} ({len(args)} > {MAX_ARGS_LEN})",
                )
            return None
        if len(self._deque) >= self.max_pending:
            logger.warning(
                "[INTENT DROPPED — queue full max=%d] tool=%s",
                self.max_pending,
                tool,
            )
            if ind is not None:
                ind.notify(
                    IndicationKind.ACTION_REJECTED,
                    body=f"queue full ({self.max_pending}): {tool}",
                )
            return None
        intent = Intent(
            id=self._next_id,
            tool=tool,
            args=args,
            raw=payload,
            decision_id=decision_id,
            transcript_id=transcript_id,
            language=language,
        )
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


OnResult = Callable[[Intent, str, dict], Awaitable[None]]
OnError = Callable[[Intent, BaseException], Awaitable[None]]


def _action_description(intent: Intent) -> str:
    """Dispatch prompt for the Claude agent SDK.

    Shape: ``Use the <Tool> tool: <args>\\n\\n<summary directive>``.
    `<Tool>` is the SDK's CamelCase identifier (Bash, Read, WebFetch, …) —
    looked up in INTENT_TOOL_TO_SDK; unknown names pass through unchanged
    (IntentQueue.submit already rejects tools outside ALLOWED_TOOLS).
    The trailing directive forces Claude to emit a Ukrainian text summary
    after tool use, otherwise Claude runs the tool and ends the turn
    silently through the Ukrainian TTS voice.
    """
    sdk_tool = INTENT_TOOL_TO_SDK.get(intent.tool, intent.tool)
    return (
        f"Use the {sdk_tool} tool: {intent.args}\n\n"
        "After the tool completes, reply with ONE concise sentence in "
        "Ukrainian (українською мовою) describing the outcome (success "
        "and key output, or failure and reason). No markdown, no code "
        "fences, just plain text."
    )


class ActionWorker:
    """Pulls intents off the queue and dispatches via direct or Claude CLI.

    Hybrid routing (US-HYB-01):
        - Simple tools (bash, read, write, web_fetch, web_search) → execute_direct() (fast)
        - Complex tools (edit, MCP tools) → claude_cli.call_action() (reasoning)
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
        self._settings = getattr(claude_cli, "settings", None)

    async def run(self) -> None:
        while True:
            intent = await self.queue.next()
            await self._process_one(intent)

    async def _process_one(self, intent: Intent) -> None:
        from .indication import IndicationKind, get_indication

        # Long-running watchdog: fires ACTION_LONG_RUNNING after 5s if the
        # action is still in flight. Self-cancels on completion.
        ind = get_indication()
        long_running_task: asyncio.Task | None = None
        if ind is not None:
            async def _watchdog() -> None:
                try:
                    await asyncio.sleep(5.0)
                except asyncio.CancelledError:
                    return
                ind.notify(
                    IndicationKind.ACTION_LONG_RUNNING,
                    body=f"{intent.tool} still running (>5s)",
                )

            long_running_task = asyncio.create_task(_watchdog())
        try:
            # Special case: workflow tool (list, run, save)
            if intent.tool == "workflow":
                await self._execute_workflow_path(intent)
            # Simple tools → direct execution, complex → Claude CLI
            elif _is_simple_tool(intent.tool):
                logger.info("[DIRECT EXECUTION tool=%s id=%d]", intent.tool, intent.id)
                await self._execute_direct_path(intent)
            else:
                logger.info("[CLAUDE CLI tool=%s id=%d]", intent.tool, intent.id)
                await self._execute_claude_path(intent)
        finally:
            if long_running_task is not None and not long_running_task.done():
                long_running_task.cancel()

    async def _execute_direct_path(self, intent: Intent) -> None:
        """Fast path: execute simple tool directly without Claude CLI."""
        logger.info(
            "[DIRECT EXECUTE tool=%s id=%d] args=%s",
            intent.tool,
            intent.id,
            intent.args[:100],  # Log first 100 chars
        )
        try:
            result = await asyncio.wait_for(
                execute_direct(intent.tool, intent.args, self._settings),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError as exc:
            logger.warning("[DIRECT TIMEOUT id=%d]", intent.id)
            await self._safe_call_error(intent, exc)
            return
        except BaseException as exc:
            await self._safe_call_error(intent, exc)
            return

        if result.get("success"):
            output = result.get("output", "")
            error = result.get("error")
            # Append stderr to output if present
            if error:
                summary = f"{output}\n{error}" if output else error
            else:
                summary = output
            logger.info(
                "[DIRECT DONE id=%d] output=%s",
                intent.id,
                output[:200] if output else "",
            )
            await self._safe_call_result(intent, summary, result)
        else:
            # Route failure through on_result so the result dict — including
            # its `spoken` key — reaches _resolve_spoken in main.py instead of
            # being discarded by _safe_call_error with a generic error phrase.
            error_msg = result.get("error", "Unknown error")
            error_summary = f"Direct tool failed: {error_msg}"
            logger.info(
                "[DIRECT FAIL id=%d] error=%s",
                intent.id,
                error_msg[:200],
            )
            await self._safe_call_result(intent, error_summary, result)

    async def _execute_workflow_path(self, intent: Intent) -> None:
        """Execute workflow commands: list, run, save."""
        store = WorkflowStore(self._settings)
        args = intent.args.strip()

        # Parse command
        if not args:
            summary = "Вкажіть команду: list, run <name>, або save <name>"
            await self._safe_call_result(intent, summary, {"success": True})
            return

        parts = args.split(maxsplit=1)
        cmd = parts[0].lower()

        if cmd == "list":
            workflows = store.list()
            if workflows:
                names = "\n".join(f"- {w.name}: {w.description}" for w in workflows)
                summary = f"Збережені робочі потоки:\n{names}"
            else:
                summary = "Немає збережених робочих потоків"
            await self._safe_call_result(intent, summary, {"success": True})

        elif cmd == "run":
            if len(parts) < 2:
                summary = "Вкажіть назву робочого потоку: run <name>"
                await self._safe_call_result(intent, summary, {"success": True})
                return

            name = parts[1].strip()
            workflow = store.get(name)
            if not workflow:
                summary = f"Робочий поток '{name}' не знайдено"
                await self._safe_call_error(intent, RuntimeError(summary))
                return

            # Execute workflow steps
            logger.info("[WORKFLOW RUN name=%s steps=%d]", name, len(workflow.steps))

            async def execute_step(tool: str, args: str) -> dict:
                """Execute a single workflow step using existing paths."""
                logger.info("[WORKFLOW STEP] tool=%s args=%s", tool, args[:100])
                if _is_simple_tool(tool):
                    result = await execute_direct(tool, args, self._settings)
                    logger.info(
                        "[WORKFLOW STEP DONE] tool=%s success=%s output=%s",
                        tool,
                        result.get("success"),
                        result.get("output", "")[:100] if result.get("output") else "",
                    )
                    return result
                else:
                    # Complex tool: use Claude CLI path
                    desc = _action_description(Intent(
                        id=0, tool=tool, args=args, raw={}
                    ))
                    result = await self.claude_cli.call_action(desc)
                    logger.info(
                        "[WORKFLOW STEP DONE] tool=%s summary=%s",
                        tool,
                        result.get("summary", "")[:100] if result.get("summary") else "",
                    )
                    return {"success": True, "output": result.get("summary", "")}

            try:
                results = await asyncio.wait_for(
                    run_workflow(workflow, execute_step),
                    timeout=self.timeout,
                )
                # Summarize results
                success_count = sum(1 for r in results if r.get("success"))
                total = len(results)
                summary = f"Виконано {success_count}/{total} кроків робочого потоку '{name}'"
                await self._safe_call_result(intent, summary, {"success": True})
            except asyncio.TimeoutError:
                summary = f"Робочий поток '{name}' перевищив ліміт часу"
                await self._safe_call_error(intent, RuntimeError(summary))

        elif cmd == "save":
            # For now, save requires CLI - workflow files are JSON
            summary = "Щоб зберегти робочий поток, створіть файл у ~/.heare/workflows/<name>.json"
            await self._safe_call_result(intent, summary, {"success": True})

        else:
            summary = f"Невідома команда: {cmd}. Доступні: list, run, save"
            await self._safe_call_result(intent, summary, {"success": True})

    async def _execute_claude_path(self, intent: Intent) -> None:
        """Reasoning path: execute via Claude CLI (edit, MCP tools)."""
        description = _action_description(intent)
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
        result_dict = result if isinstance(result, dict) else {}
        await self._safe_call_result(intent, summary, result_dict)

    async def _safe_call_result(self, intent: Intent, summary: str, result: dict) -> None:
        try:
            await self.on_result(intent, summary, result)
        except Exception:
            logger.exception("on_result raised for intent id=%d (swallowed)", intent.id)

    async def _safe_call_error(self, intent: Intent, exc: BaseException) -> None:
        try:
            await self.on_error(intent, exc)
        except Exception:
            logger.exception("on_error raised for intent id=%d (swallowed)", intent.id)
