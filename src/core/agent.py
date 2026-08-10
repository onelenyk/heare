"""Hands — the agent that acts, off the conversational path.

Two agents, one model, one key. The split is temporal, not intellectual:

* **Voice** answers within a second and holds the conversation. It sees
  three verbs, so choosing one is trivial.
* **Hands** sees every tool and has no deadline. It runs as a plain
  asyncio task, not as a pipeline stage, so nothing in the speaking path
  ever waits for it.

The measured reason: on this machine a turn with a tool call takes
3822 ms to first audio against 1351 ms without one. That 2.5 s is silence
the user hears as the assistant being stuck. Delegating turns it into two
utterances — "гляну" now, the answer when it lands — and the speaking
path stops waiting for anything at all.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

MAX_STEPS = 12
SYSTEM = """\
You do the work. You are not speaking to the user — another assistant
will read your answer aloud, so return plain prose with no markdown, no
lists and no code fences.

Use the tools to find out rather than guessing. When you are done,
answer in one or two sentences: what you found or what you did. If you
could not do it, say plainly what stopped you.
"""


class Hands:
    """Runs delegated work and delivers the result back into the talk."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._deliver: Callable[[str], Awaitable[None]] | None = None
        self._running: set[asyncio.Task] = set()

    def set_delivery(self, deliver: Callable[[str], Awaitable[None]]) -> None:
        """Wired after the pipeline exists — that is what results land in."""
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
        except Exception as exc:
            logger.exception("hands: %s", task)
            result = f"Не вдалося: {exc}"
        logger.info("hands done: %.160s", result)
        if self._deliver is not None:
            await self._deliver(result)

    async def _loop(self, task: str) -> str:
        from src.core import tools as core_tools

        messages: list[dict] = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": task},
        ]
        schemas = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": {
                        "type": "object",
                        "properties": t.properties,
                        "required": t.required,
                    },
                },
            }
            for t in core_tools.REGISTRY.values()
            if not t.voice_only
        ]

        async with httpx.AsyncClient(timeout=120) as client:
            for step in range(MAX_STEPS):
                reply = await self._chat(client, messages, schemas)
                calls = reply.get("tool_calls") or []
                if not calls:
                    return (reply.get("content") or "").strip() or "Готово."

                messages.append(reply)
                for call in calls:
                    fn = call["function"]
                    name = fn["name"]
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    tool = core_tools.REGISTRY.get(name)
                    logger.info("hands step %d: %s(%s)", step + 1, name, args)
                    if tool is None:
                        out = f"no such tool: {name}"
                    else:
                        try:
                            out = await asyncio.wait_for(
                                tool.fn(**args), timeout=tool.timeout
                            )
                        except asyncio.TimeoutError:
                            out = f"{name} timed out"
                        except Exception as exc:
                            out = f"{name} failed: {exc}"
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": str(out)[:6000],
                        }
                    )
        return "Не вклався в ліміт кроків."

    async def _chat(
        self, client: httpx.AsyncClient, messages: list[dict], schemas: list[dict]
    ) -> dict:
        s = self._settings
        if s.zai_api_key:
            url, key, model = s.zai_base_url, s.zai_api_key, s.zai_model
        else:
            url, key, model = s.deepseek_base_url, s.deepseek_api_key, s.deepseek_model
        resp = await client.post(
            f"{url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "messages": messages, "tools": schemas},
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]
