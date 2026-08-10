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

logger = logging.getLogger("heare.hands")

MAX_STEPS = 12
RESULT_PREFIX = "[результат роботи]"

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
    ) -> None:
        self._settings = settings
        self._llm_service = llm_service
        self._session_state = session_state
        self._conversation_manager = conversation_manager
        self._deliver: Callable[[str], Awaitable[None]] | None = None
        self._running: set[asyncio.Task] = set()

    def set_delivery(self, deliver: Callable[[str], Awaitable[None]]) -> None:
        """Wired once the pipeline exists — results land there."""
        self._deliver = deliver

    @property
    def busy(self) -> int:
        return len(self._running)

    def start(self, task: str) -> None:
        """Begin work and return immediately. Never awaits the job."""
        job = asyncio.create_task(self._run(task))
        self._running.add(job)
        job.add_done_callback(self._running.discard)

    async def _run(self, task: str) -> None:
        try:
            result = await self._loop(task)
        except Exception as exc:  # a failed job must still produce a reply
            logger.exception("hands: %s", task)
            result = f"Не вдалося: {exc}"
        logger.info("[HANDS] done: %.200s", result)
        if self._deliver is None:
            logger.warning("[HANDS] no delivery wired — result dropped")
            return
        await self._deliver(f"{RESULT_PREFIX} {result}")

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

    async def _loop(self, task: str) -> str:
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": task},
        ]
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
