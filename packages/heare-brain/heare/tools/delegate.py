"""Delegation: hand work to an agent, once per distinct request."""

from __future__ import annotations

from typing import Any, Callable

from heare.core.forms import Task
from heare.core.state import State


async def run(
    start: Callable[[str], Task], state: State, words: Any, arguments: dict
) -> str:
    request = str(arguments.get("task", "")).strip()
    if not request:
        return "Скажи, що саме зробити."
    if same(state, words, request) is not None:
        return "Це вже в роботі."
    start(request)
    return "Беру."


def same(state: State, words: Any, request: str) -> Task | None:
    """A piece of work already running that this request repeats. One
    request is one job: asked again before it is done, it is the same
    job, not a second one."""
    asked = set(words.tokens(request))
    for task in state.tasks.values():
        if task.status != "running":
            continue
        running = set(words.tokens(task.request))
        common = len(asked & running)
        if common and common >= 0.5 * min(len(asked), len(running)):
            return task
    return None
