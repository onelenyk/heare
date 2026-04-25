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
from typing import Any, Awaitable, Callable

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
        # CCS-05a: optional callbacks wired by the daemon at startup.
        # ``conversation_manager`` exposes record_action_cancelled to mark
        # drained intents in the action log. ``cancel_in_flight_callback``
        # is the worker's hook to abort the currently-running action;
        # Story 5b will replace the stub with real kill paths.
        self.conversation_manager: Any | None = None
        self.cancel_in_flight_callback: Callable[[], Awaitable[bool]] | None = None

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

    def bind_worker(
        self, cancel_in_flight: Callable[[], Awaitable[bool]] | None
    ) -> None:
        """CCS-05a: register the worker's in-flight cancel hook.

        Called by ActionWorker on construction. Story 5b will provide the
        real kill semantics; for 5a a None or no-op callable is fine.
        """
        self.cancel_in_flight_callback = cancel_in_flight

    def bind_conversation_manager(self, manager: Any | None) -> None:
        """CCS-05a: register the ConversationManager so cancel_active can
        update the action log via record_action_cancelled.
        """
        self.conversation_manager = manager

    async def cancel_active(self) -> bool:
        """CCS-05a: drain pending intents and abort any in-flight action.

        Behaviour:
          * Pops every pending intent from the queue. For each, marks
            ``status='cancelled'`` in the action log via the bound
            conversation_manager (if any).
          * Calls the bound ``cancel_in_flight_callback`` to abort whatever
            the worker is currently executing. Story 5a leaves this as a
            stub (Story 5b implements real kill paths). When unbound, logs
            a debug warning that 5b has not shipped.
          * Returns True iff anything was cancelled (queued or in-flight).
            On True, fires IndicationKind.INTENT_CANCELLED exactly once.
            On False (empty queue + no in-flight), logs debug, fires NO
            indication, and returns.

        Latency: synchronous deque drain + one optional await on the
        worker callback + one notify(). Indication.notify() is sync (the
        facade enqueues the dispatch via call_soon_threadsafe), so the
        only real await here is the in-flight callback — which is a
        no-op in 5a.
        """
        from .indication import IndicationKind, get_indication

        drained: list[Intent] = []
        while self._deque:
            drained.append(self._deque.popleft())
        # Drained the whole deque; clear the wake event for queue consumers.
        self._event.clear()

        # Mark each drained intent as cancelled in the action log so the
        # next-turn context reflects reality. Best-effort — failures are
        # swallowed so a broken store can't block cancellation.
        manager = self.conversation_manager
        if manager is not None:
            for intent in drained:
                try:
                    manager.record_action_cancelled(
                        intent.id, tool=intent.tool, args=intent.args
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "record_action_cancelled raised for intent id=%d (swallowed)",
                        intent.id,
                    )

        in_flight_cancelled = False
        cb = self.cancel_in_flight_callback
        if cb is not None:
            try:
                in_flight_cancelled = bool(await cb())
            except Exception:  # noqa: BLE001
                logger.exception(
                    "cancel_in_flight_callback raised (swallowed) — Story 5b "
                    "will replace this stub with real kill semantics"
                )
        else:
            logger.debug(
                "cancel_active: no cancel_in_flight_callback bound — "
                "Story 5b kill paths not yet wired"
            )

        anything = bool(drained) or in_flight_cancelled
        if not anything:
            logger.debug("cancel_active: empty queue + no in-flight; no-op")
            return False

        ind = get_indication()
        if ind is not None:
            ind.notify(
                IndicationKind.INTENT_CANCELLED,
                body=f"cancelled {len(drained)} pending"
                + (" + in-flight" if in_flight_cancelled else ""),
            )
        return True


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
        *,
        tts_cancel: Callable[[], int] | None = None,
    ) -> None:
        self.queue = queue
        self.claude_cli = claude_cli
        self.on_result = on_result
        self.on_error = on_error
        self.timeout = timeout
        self._settings = getattr(claude_cli, "settings", None)
        # CCS-05b: optional callable invoked by ``cancel_in_flight`` to drop
        # queued/in-flight TTS frames with a 50ms fade-out. Returns the
        # number of frames dropped. None disables the path (default for
        # tests that have no TTS service wired).
        self._tts_cancel = tts_cancel
        # CCS-05b: bookkeeping for the in-flight cancel hook. ``_active_task``
        # is the asyncio.Task currently executing one intent in
        # ``_process_one``; ``_active_intent`` is the intent that task is
        # processing. Both are None whenever the worker is parked on
        # ``queue.next()``.
        self._active_task: asyncio.Task | None = None
        self._active_intent: Intent | None = None
        # Auto-bind the queue's in-flight cancel hook so callers that build
        # a worker manually (tests, future entrypoints) get the wiring
        # without an extra step. main.py keeps the explicit bind as
        # defence-in-depth.
        try:
            queue.bind_worker(self.cancel_in_flight)
        except Exception:  # noqa: BLE001 — best-effort wiring
            logger.exception("queue.bind_worker raised (swallowed)")

    async def run(self) -> None:
        while True:
            intent = await self.queue.next()
            self._active_intent = intent
            self._active_task = asyncio.current_task()
            try:
                await self._process_one(intent)
            except asyncio.CancelledError:
                # cancel_in_flight propagated cancellation into the active
                # task. Mark the intent cancelled in the action log via
                # on_error so downstream observers (TTS hint, etc.) still
                # fire, then keep the worker loop alive — the cancelled
                # intent is fully unwound here, the next ``await
                # queue.next()`` resumes normal service.
                logger.info(
                    "[ACTION CANCELLED id=%d tool=%s]", intent.id, intent.tool
                )
                try:
                    await self._safe_call_error(intent, asyncio.CancelledError())
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "on_error raised for cancelled intent id=%d", intent.id
                    )
            finally:
                self._active_task = None
                self._active_intent = None

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

    # ------------------------------------------------------------------
    # CCS-05b — in-flight cancel
    # ------------------------------------------------------------------

    async def cancel_in_flight(self) -> bool:
        """Cancel the currently-executing intent if any.

        Story 5b kill-path: cancels the worker's active asyncio task so the
        ``CancelledError`` propagates into whatever the task is awaiting —
        bash subprocess.communicate (which calls ``os.killpg`` on its
        SIGTERM/SIGKILL escalation), httpx requests (which surface the
        cancellation as ``CancelledError``), or the Claude SDK call (best
        effort — wrapped in a 1s timeout). Also drops queued/in-flight TTS
        frames via the bound ``tts_cancel`` callable so the user does not
        keep hearing an obsolete answer.

        Returns True iff a cancellation was actually issued, i.e. there
        was an active task to cancel.
        """
        active = self._active_task
        intent = self._active_intent
        if active is None or active.done():
            return False

        # Mark the intent as cancelled in the action log first — even if the
        # task takes a while to finish unwinding, the action log row already
        # reflects user intent. Best-effort: a missing/None manager just
        # leaves the row at its prior status.
        manager = getattr(self.queue, "conversation_manager", None)
        if manager is not None and intent is not None:
            try:
                manager.record_action_cancelled(
                    intent.id, tool=intent.tool, args=intent.args
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "record_action_cancelled raised for intent id=%d (swallowed)",
                    intent.id,
                )

        # Drop queued/in-flight TTS audio with a 50ms fade-out so the user
        # doesn't continue hearing a stale answer. Best-effort.
        if self._tts_cancel is not None:
            try:
                dropped = self._tts_cancel()
                logger.info(
                    "[CANCEL TTS dropped=%s intent_id=%s]",
                    dropped,
                    intent.id if intent is not None else "?",
                )
            except Exception:  # noqa: BLE001
                logger.exception("tts_cancel raised (swallowed)")

        # Best-effort SDK cancel. The Claude SDK exposes
        # ``kill_running_action`` for in-flight aborts; honor it with a
        # 1s timeout so a wedged SDK can't block the cancel path.
        kill = getattr(self.claude_cli, "kill_running_action", None)
        if kill is not None:
            try:
                await asyncio.wait_for(kill(), timeout=1.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "kill_running_action timed out after 1.0s — proceeding"
                )
            except Exception:  # noqa: BLE001
                logger.exception("kill_running_action raised (swallowed)")

        # Cancel the asyncio task. CancelledError propagates into the
        # subprocess.communicate / httpx / SDK await; the bash path catches
        # it and runs the SIGTERM→SIGKILL escalation via os.killpg before
        # re-raising.
        active.cancel()

        # Brief courtesy wait so callers (and tests) see the task actually
        # unwind. Don't hold the cancel path forever — if the task is
        # stuck, the bash kill_pg has already fired, and the worker loop's
        # own CancelledError handler will mark the intent and continue.
        try:
            await asyncio.wait_for(asyncio.shield(active), timeout=0.1)
        except (asyncio.TimeoutError, asyncio.CancelledError, BaseException):
            pass
        return True

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
