"""The catalog of abilities the model may call, assembled outside the core.

The core knows the shape of a tool (``heare.core.forms.Tools``) and runs
whichever one is offered; what the tools are, and when each may be
offered, lives here.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from heare.core.forms import Memory, Task
from heare.core.state import State


class Toolbox:
    def __init__(
        self,
        *,
        memory: Memory | None,
        state: State,
        delegate: Callable[[str], Task],
        stop: Callable[[], int],
        words: Any,
        has_executors: Callable[[], bool],
    ) -> None:
        self._memory = memory
        self._state = state
        self._delegate = delegate
        self._stop = stop
        self._words = words
        self._has_executors = has_executors

    def verbs(self) -> dict[str, tuple[dict, Callable[[dict], Awaitable[str]]]]:
        verbs: dict[str, tuple[dict, Callable[[dict], Awaitable[str]]]] = {}
        if self._memory is not None:
            verbs["recall"] = (_RECALL, self._run_recall)
        if self._has_executors():
            verbs["delegate"] = (_DELEGATE, self._run_delegate)
        if any(task.status == "running" for task in self._state.tasks.values()):
            verbs["stop_work"] = (_STOP, self._run_stop)
        return verbs

    def acknowledging(self) -> set[str]:
        return {"delegate", "stop_work"}

    def read_only(self, name: str) -> bool:
        return name == "recall"

    async def _run_recall(self, arguments: dict) -> str:
        query = str(arguments.get("query", "")).strip()
        if not query:
            return "Що саме згадати?"
        return await self._memory.recall(
            query, str(arguments.get("when", "") or "")
        )

    async def _run_delegate(self, arguments: dict) -> str:
        request = str(arguments.get("task", "")).strip()
        if not request:
            return "Скажи, що саме зробити."
        if self._same(request) is not None:
            return "Це вже в роботі."
        self._delegate(request)
        return "Беру."

    async def _run_stop(self, arguments: dict) -> str:
        return "Зупинив." if self._stop() else "Нічого не було запущено."

    def _same(self, request: str) -> Task | None:
        """A piece of work already running that this request repeats. One
        request is one job: asked again before it is done, it is the same
        job, not a second one."""
        asked = set(self._words.tokens(request))
        for task in self._state.tasks.values():
            if task.status != "running":
                continue
            running = set(self._words.tokens(task.request))
            common = len(asked & running)
            if common and common >= 0.5 * min(len(asked), len(running)):
                return task
        return None


_RECALL = {
    "type": "function",
    "function": {
        "name": "recall",
        "description": (
            "Search what was actually said in your past conversations with "
            "this person — whenever they ask what they told you, what you "
            "agreed, or when something came up."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The words to look for — the subject, "
                                   "not the whole question.",
                },
                "when": {
                    "type": "string",
                    "description": "Their own words about when, if they said "
                                   "any: «вчора», «минулого тижня». Leave out "
                                   "if they did not.",
                },
            },
            "required": ["query"],
        },
    },
}

_STOP = {
    "type": "function",
    "function": {
        "name": "stop_work",
        "description": (
            "Stop all the work that is running now. Whenever the person asks "
            "to stop, cancel or call off what the agents are doing, in any "
            "words. Never say it is stopped without calling this."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

_DELEGATE = {
    "type": "function",
    "function": {
        "name": "delegate",
        "description": (
            "Hand work to an agent. Everything outside this conversation "
            "goes through here: files, code, the shell, the web, other "
            "programs. You cannot see or touch any of it yourself, so never "
            "say you cannot before you have asked. Returns at once: say in "
            "one short sentence that you are on it, then stop. The answer "
            "arrives later as its own message."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "What the person asked for, as they said it.",
                },
            },
            "required": ["task"],
        },
    },
}
