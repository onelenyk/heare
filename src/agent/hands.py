"""Hands — the agent that acts, off the conversational path.

Two agents, one model, one key. The split is temporal, not intellectual:

* **Voice** lives in the pipeline, sees three verbs, and must answer
  within a second. Choosing among three schemas is trivial; choosing
  among sixty-three under a latency budget is not.
* **Hands** is this module. It sees every tool, has no deadline, and runs
  as an asyncio task rather than a pipeline stage, so nothing in the
  speaking path ever waits for it.

Measured on this machine, a turn that calls a tool inline takes 3822 ms
to first audio against 1351 ms without one. Worse than the median is the
variance: run the same request twice and the assistant narrates first
once and goes silent into the tool the next time. ``delegate`` returns
instantly, so the acknowledgement is structural rather than hoped for.

Tools are executed through ``execute_direct`` and the same serializers
the pipeline uses, so delegating changes who calls a tool, never what the
tool does.

The result re-enters as a user turn (see ``set_delivery``), which means
the voice agent phrases it — in the right language, in its own voice,
through the existing TTS path. There is no second way of speaking.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

import httpx

from src.agent import jobs as jobs_mod

logger = logging.getLogger("heare.hands")

MAX_STEPS = 12
RECENT_TURNS = 6
# A job quiet for longer than this gets a non-verbal beacon. Not speech:
# speaking costs a model turn and can cut across the user, and the point
# is only to distinguish "still working" from "stuck" — which is what a
# spinner does on a screen.
PROGRESS_AFTER = 12.0
RESULT_PREFIX = "[результат роботи]"


def _label(task: str) -> str:
    """Two or three words, for telling concurrent jobs apart."""
    words = [w for w in task.split() if len(w) > 2][:3]
    return " ".join(words)[:40] or "робота"

SYSTEM = """\
You do the work. You are not speaking to the user — another assistant
will read your answer aloud, so return plain prose: no markdown, no
lists, no code fences, no file paths unless they are the answer.

Use the tools to find out rather than guessing. Prefer one decisive step
over several tentative ones.

When you are done, answer in one or two sentences: what you found or what
you did. If you could not do it, say plainly what stopped you. Never
answer with a wall of output — summarise it.
"""


class Hands:
    """Runs delegated work and delivers the result back into the talk."""

    def __init__(
        self,
        settings: Any,
        *,
        llm_service: Any = None,
        session_state: Any = None,
        conversation_manager: Any = None,
        recent_turns: Callable[[], list[dict]] | None = None,
        jobs: Any = None,
    ) -> None:
        self._settings = settings
        self._llm_service = llm_service
        self._session_state = session_state
        self._conversation_manager = conversation_manager
        # Reads the live conversation. Without it the worker sees one
        # sentence and the voice agent has to restate everything it knows
        # — which it does badly, inventing requirements as it goes.
        self._recent_turns = recent_turns
        # Persistence, so a job outlives the turn and the process. Without
        # it the assistant cannot say what it did an hour ago, and a
        # restart mid-job leaves the user waiting for an answer that can
        # never arrive.
        self._jobs = jobs
        self._deliver: Callable[[str], Awaitable[None]] | None = None
        self._running: dict[asyncio.Task, str] = {}

    def set_delivery(self, deliver: Callable[[str], Awaitable[None]]) -> None:
        """Wired once the pipeline exists — results land there."""
        self._deliver = deliver

    @property
    def busy(self) -> int:
        return len(self._running)

    def start(self, task: str) -> None:
        """Begin work and return immediately. Never awaits the job."""
        job = asyncio.create_task(self._run(task, _label(task)))
        self._running[job] = _label(task)
        job.add_done_callback(self._running.pop)

    def cancel_all(self) -> int:
        """Stop everything in flight. "Стоп" has to mean stop.

        Without this the assistant falls silent when interrupted and then,
        seconds later, announces the result of work the user just called
        off — which reads as not listening.
        """
        jobs = [j for j in self._running if not j.done()]
        for job in jobs:
            job.cancel()
        if jobs:
            logger.info("[HANDS] cancelled %d job(s)", len(jobs))
        return len(jobs)

    async def _run(self, task: str, label: str) -> None:
        beacon = asyncio.create_task(self._beacon(label))
        job_id = await self._job_start(label, task)
        try:
            result = await self._loop(task, job_id)
        except asyncio.CancelledError:
            logger.info("[HANDS] %s: cancelled", label)
            await self._job_finish(job_id, jobs_mod.CANCELLED)
            raise
        except Exception as exc:  # a failed job must still produce a reply
            logger.exception("hands: %s", task)
            result = f"Не вдалося: {exc}"
            await self._job_finish(job_id, jobs_mod.FAILED, error=repr(exc))
        else:
            await self._job_finish(job_id, jobs_mod.DONE, result=result)
        finally:
            beacon.cancel()
        logger.info("[HANDS] done: %.200s", result)
        if self._deliver is None:
            logger.warning("[HANDS] no delivery wired — result dropped")
            return
        # The label only appears when more than one job is in flight:
        # naming a single result is noise, but two unlabelled results
        # arriving out of order are indistinguishable.
        tag = f" [{label}]" if len(self._running) > 1 else ""
        await self._deliver(f"{RESULT_PREFIX}{tag} {result}")

    # -- the loop ------------------------------------------------------

    def _tool_schemas(self) -> list[dict]:
        """Every enabled tool except ``delegate`` — no handing work back.

        The mode profile is applied here. It used to gate the
        conversational model's tools; with the work moved to the worker,
        not re-checking it would quietly turn every mode into "allow
        everything" — a security control lost to a refactor.
        """
        from src.agent.modes import is_tool_allowed as mode_is_tool_allowed
        from src.agent.tools.system import TOOLS

        profile = getattr(self._session_state, "profile", None)

        def allowed(name: str) -> bool:
            if name == "delegate":
                return False
            if profile is None:
                return True
            return mode_is_tool_allowed(profile, name)

        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": {
                        "type": "object",
                        "properties": t.schema_fields,
                        "required": t.required,
                    },
                },
            }
            for t in TOOLS
            if t.enabled and allowed(t.name)
        ]

    async def _execute(self, name: str, arguments: dict) -> str:
        """Run one tool through the pipeline's own execution path.

        The mode gate and the action log live in the pipeline's handler
        wrapper, which the worker does not go through. Repeating them here
        is deliberate: without the gate every mode would quietly become
        "allow everything", and without the log the actions table would go
        back to being empty — the state that made the assistant's own work
        invisible for months.
        """
        from src.agent.modes import mode_gate_refusal
        from src.agent.tools.direct import execute_direct
        from src.agent.tools.system import _SERIALIZERS, tool_timeout_secs

        serializer = _SERIALIZERS.get(name)
        args_str = serializer(arguments) if serializer else json.dumps(arguments)

        refusal = mode_gate_refusal(self._session_state, name)
        if refusal is not None:
            return f"{name} refused: {refusal.get('error', 'blocked by mode')}"

        intent_id = self._record_pending(name, args_str)
        try:
            result = await asyncio.wait_for(
                execute_direct(name, args_str, self._settings),
                timeout=tool_timeout_secs(name),
            )
        except asyncio.TimeoutError:
            self._record_error(intent_id, "timed out")
            return f"{name} timed out"
        except Exception as exc:  # noqa: BLE001
            logger.exception("[HANDS] %s failed", name)
            self._record_error(intent_id, repr(exc))
            return f"{name} failed: {exc}"

        if isinstance(result, dict):
            if result.get("success"):
                output = str(result.get("output", ""))[:6000] or "done"
                self._record_result(intent_id, output)
                return output
            error = str(result.get("error", "unknown error"))
            self._record_error(intent_id, error)
            return f"{name} failed: {error}"
        text = str(result)[:6000]
        self._record_result(intent_id, text)
        return text

    async def _beacon(self, label: str) -> None:
        """Sound, not speech, while a long job is still running.

        The voice agent sees only the start and the end of a job, so a
        slow one is indistinguishable from a stuck one. Saying so out loud
        would cost a model turn and risk talking over the user; a cue is
        the audible equivalent of a spinner.
        """
        try:
            from src.voice.indication.core import IndicationKind, get_indication
        except Exception:
            return
        try:
            while True:
                await asyncio.sleep(PROGRESS_AFTER)
                indication = get_indication()
                if indication is None:
                    return
                logger.info("[HANDS] still working: %s", label)
                indication.notify(
                    IndicationKind.ACTION_LONG_RUNNING,
                    title="heare",
                    body=label,
                )
        except asyncio.CancelledError:
            raise

    # -- the action log ------------------------------------------------

    def _record_pending(self, tool: str, args: str) -> int | None:
        if self._conversation_manager is None:
            return None
        from src.agent.tools.system import _intent_id_seq

        intent_id = next(_intent_id_seq)
        try:
            self._conversation_manager.record_action_pending(intent_id, tool, args)
        except Exception:
            logger.exception("[HANDS] record_action_pending failed (non-fatal)")
        return intent_id

    def _record_result(self, intent_id: int | None, summary: str) -> None:
        if intent_id is None or self._conversation_manager is None:
            return
        try:
            self._conversation_manager.record_action_result(intent_id, summary)
        except Exception:
            logger.exception("[HANDS] record_action_result failed (non-fatal)")

    def _record_error(self, intent_id: int | None, error: str) -> None:
        if intent_id is None or self._conversation_manager is None:
            return
        try:
            self._conversation_manager.record_action_error(intent_id, error)
        except Exception:
            logger.exception("[HANDS] record_action_error failed (non-fatal)")

    def _conversation(self) -> str:
        """The last few turns, so "that file" and "the one we discussed"
        mean something to the worker."""
        if self._recent_turns is None:
            return ""
        try:
            turns = self._recent_turns()
        except Exception:
            logger.exception("[HANDS] recent turns unavailable (non-fatal)")
            return ""
        lines = []
        for turn in turns[-RECENT_TURNS:]:
            role = turn.get("role")
            content = turn.get("content")
            if role in ("user", "assistant") and isinstance(content, str) and content:
                who = "User" if role == "user" else "Assistant"
                lines.append(f"{who}: {content.strip()[:400]}")
        if not lines:
            return ""
        return (
            "Recent conversation, for reference — the user may be "
            "referring to something in it:\n" + "\n".join(lines)
        )

    async def _job_start(self, label: str, task: str) -> int | None:
        if self._jobs is None:
            return None
        return await self._jobs.start(label, task)

    async def _job_step(self, job_id: int | None, text: str) -> None:
        if self._jobs is not None:
            await self._jobs.step(job_id, text)

    async def _job_finish(
        self,
        job_id: int | None,
        state: str,
        *,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        if self._jobs is not None:
            await self._jobs.finish(job_id, state, result=result, error=error)

    async def _loop(self, task: str, job_id: int | None = None) -> str:
        messages: list[dict] = [{"role": "system", "content": SYSTEM}]
        conversation = self._conversation()
        if conversation:
            messages.append({"role": "system", "content": conversation})
        messages.append({"role": "user", "content": task})
        schemas = self._tool_schemas()

        async with httpx.AsyncClient(timeout=120) as client:
            for step in range(MAX_STEPS):
                reply = await self._chat(client, messages, schemas)
                calls = reply.get("tool_calls") or []
                if not calls:
                    return (reply.get("content") or "").strip() or "Готово."

                messages.append(reply)
                for call in calls:
                    fn = call.get("function", {})
                    name = fn.get("name", "")
                    try:
                        arguments = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    logger.info("[HANDS] step %d: %s(%s)", step + 1, name, arguments)
                    await self._job_step(job_id, f"{name}({arguments})")
                    output = await self._execute(name, arguments)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id", ""),
                            "content": output,
                        }
                    )
        return "Не вклався в ліміт кроків."

    async def _chat(
        self, client: httpx.AsyncClient, messages: list[dict], schemas: list[dict]
    ) -> dict:
        """One completion against whichever provider the daemon is using."""
        base_url, api_key, model = self._provider()
        resp = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "messages": messages, "tools": schemas},
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]

    def _provider(self) -> tuple[str, str, str]:
        """Follow the live provider so hands and voice never diverge."""
        from src.agent.llm.providers import PROVIDERS

        key = None
        if self._llm_service is not None:
            key = getattr(self._llm_service, "active_provider", None)
        if key not in PROVIDERS:
            key = next(
                (
                    name
                    for name, cfg in PROVIDERS.items()
                    if getattr(self._settings, cfg.api_key_attr, None)
                ),
                None,
            )
        if key is None:
            raise RuntimeError("no LLM provider configured for hands")

        cfg = PROVIDERS[key]
        # Settings may override the registry defaults per provider
        # (deepseek_base_url, deepseek_model, ...); fall back to the
        # registry so a provider works the moment its key is present.
        base_url = getattr(self._settings, f"{key}_base_url", "") or cfg.base_url
        model = getattr(self._settings, f"{key}_model", "") or cfg.default_model
        api_key = getattr(self._settings, cfg.api_key_attr, "") or ""
        return base_url, api_key, model


_hands: Hands | None = None


def set_hands(hands: Hands | None) -> None:
    """Module-level handle, so the ``delegate`` tool can reach the worker."""
    global _hands
    _hands = hands


def get_hands() -> Hands | None:
    return _hands


async def execute_delegate(args: str, settings: Any = None) -> dict:
    """The ``delegate`` tool. Starts the work and returns at once."""
    task = (args or "").strip()
    if not task:
        return {"success": False, "output": "", "error": "no task given"}
    if _hands is None:
        return {
            "success": False,
            "output": "",
            "error": "worker unavailable",
            "spoken": {
                "en": "My worker is not running.",
                "uk": "Виконавець не запущений.",
            },
        }

    _hands.start(task)
    logger.info("[HANDS] started: %.200s", task)
    return {
        "success": True,
        "output": "started; tell the user you are on it, in one short sentence",
    }
