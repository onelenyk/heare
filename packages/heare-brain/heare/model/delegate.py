"""The model that hands work out: who does it, what exactly, what came of it.

The model that talks only decides that something is a job. Everything
about the job is decided here, in the background, after the person has
already heard «Беру.» — so none of it slows the conversation down.
"""

from __future__ import annotations

import logging
from typing import Any

from heare.core import Executor, Task
from heare.model.llm import resolve_llm, stream_chat_events

logger = logging.getLogger("heare.delegate")

# Longer than this is not something to say aloud; it gets cut to the point.
SPEAKABLE_CHARS = 280

_ASSIGN = (
    "You hand out work. A person asked the assistant for something in a talk. "
    "Write the task for an executor so that it is clear without the talk: "
    "what exactly to do and what to give back. Take from the talk all that "
    "makes the request precise (which file, project or thing it is about). "
    "Make nothing up. The executor answers briefly, to the point, in the "
    "language the person spoke.\n\n"
    "The first line of the answer is only the executor's name from the list. "
    "Then the task."
)
_BRIEF = (
    "Here is an executor's result. Cut it to one to three sentences that can "
    "be said aloud, in the language the person spoke: what came of it and "
    "the most important details — numbers, names, what failed. No markup, "
    "lists or code. Add nothing of your own."
)


class Dispatcher:
    def __init__(self, settings: Any) -> None:
        self._settings = settings

    async def ask(self, system: str, user: str) -> str:
        # Its own provider if one is named, otherwise the one that talks.
        provider = str(getattr(self._settings, "delegate_provider", "") or "") or None
        cfg = resolve_llm(self._settings, provider)
        parts: list[str] = []
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        async for event in stream_chat_events(messages, cfg, temperature=0.2):
            if event.get("type") == "delta":
                parts.append(event["text"])
        return "".join(parts).strip()

    async def assign(
        self, request: str, history: list[dict], executors: list[Executor]
    ) -> tuple[str, str]:
        names = [e.name for e in executors]
        if len(names) == 1 and not history:
            return names[0], request
        roster = "\n".join(f"- {e.name}: {e.about}" for e in executors)
        talk = "\n".join(f"{m['role']}: {m['content']}" for m in history)
        try:
            answer = await self.ask(
                _ASSIGN,
                f"Executors:\n{roster}\n\nThe talk:\n{talk}\n\nRequest: {request}",
            )
        except Exception:
            logger.exception("could not write the task — handing the request over as said")
            return names[0], request
        first, _, rest = answer.partition("\n")
        chosen = next((n for n in names if n in first.lower()), None)
        if chosen is None:
            # It skipped the name: the whole answer is the task.
            return names[0], answer or request
        return chosen, rest.strip() or request

    async def brief(self, task: Task) -> str:
        if len(task.result) <= SPEAKABLE_CHARS:
            return task.result
        try:
            return await self.ask(
                _BRIEF, f"Request: {task.request}\n\nResult:\n{task.result}"
            ) or task.result[:SPEAKABLE_CHARS]
        except Exception:
            logger.exception("could not cut the result down")
            return task.result[:SPEAKABLE_CHARS]
