"""Which hands get a piece of work: a rule, not a model.

The dispatcher asked a model who should do each job — a second or two on
every one, and now and then a guess. The rule is short:

1. work that goes on from the last goes to the hands that did the last;
2. otherwise the native hands, when they are here, take it first. They call
   the model anyway, so «mine or not» costs nothing: what is not theirs they
   hand on (``Handoff``) as they worded it, to the other hands;
3. otherwise the first hands there are.

The task is the person's words and the last turns of the talk — what the
dispatcher's model used to write out, without the model.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from heare.core import Executor, Task
from heare.model.delegate import Dispatcher
from heare.hand import Handoff

NATIVE = "native-hands"
# How long work waits for native hands that are turned on here but not back
# yet: after a restart the brain is up seconds before its parts are.
WAIT_S = 10.0


def with_talk(request: str, history: list[dict]) -> str:
    talk = "\n".join(f"{m['role']}: {m['content']}" for m in history if m.get("content"))
    return f"{request}\n\nОстанні репліки розмови:\n{talk}" if talk else request


class Router(Dispatcher):
    """Assigns by the rule; cutting a long result down is still the model's."""

    def __init__(self, settings: Any, tasks: Callable[[], dict[int, Task]],
                 expected: Callable[[], bool] = lambda: False,
                 present: Callable[[], list[str]] = list) -> None:
        """expected: whether native hands are turned on here; present: the
        hands there are now, looked at again while waiting for them."""
        super().__init__(settings)
        self._tasks = tasks
        self._expected = expected
        self._present = present

    async def assign(
        self, request: str, history: list[dict], executors: list[Executor]
    ) -> tuple[str, str]:
        names = [e.name for e in executors]
        task = with_talk(request, history)
        tasks = list(self._tasks().values())
        # The one being assigned is the newest with nobody on it yet.
        current = next((t for t in reversed(tasks) if not t.executor), None)
        if current is not None and current.follows:
            last = next((t for t in reversed(tasks) if t.executor in names), None)
            if last is not None:
                return last.executor, task
        if NATIVE not in names and self._expected():
            names = await self._wait_for_native(names)
        if NATIVE in names:
            return NATIVE, task
        return names[0], task

    async def _wait_for_native(self, names: list[str]) -> list[str]:
        loop = asyncio.get_running_loop()
        end = loop.time() + WAIT_S
        while loop.time() < end:
            now = self._present()
            if NATIVE in now:
                return now
            await asyncio.sleep(0.2)
        return names


class FirstHand:
    """The native hands, and whoever takes what they hand on."""

    def __init__(self, inner: Any, others: Callable[[], list[Executor]],
                 tasks: Callable[[], dict[int, Task]] = dict) -> None:
        self._inner = inner
        self._others = others
        self._tasks = tasks

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def about(self) -> str:
        return self._inner.about

    async def run(self, task: str, desk: Any) -> str:
        try:
            return await self._inner.run(task, desk)
        except Handoff as handoff:
            other = next((e for e in self._others() if e.name != self.name), None)
            if other is None:
                return "Це не для швидких рук, а інших рук зараз немає."
            # The page shows who does the work: from here on, the other hands.
            for found in self._tasks().values():
                if (found.executor == self.name and found.status == "running"
                        and found.request == getattr(desk, "asked", None)):
                    found.executor = other.name
            desk.step(f"передано від {self.name}")
            return await other.run(handoff.task, desk)

    def __getattr__(self, name: str) -> Any:
        # The door talks to the body behind it: claim, step, permit, result.
        return getattr(self._inner, name)


def executor(name: str, about: str, make: Callable[[str, str], Any],
             others: Callable[[], list[Executor]],
             tasks: Callable[[], dict[int, Task]] = dict) -> Any:
    """The executor for a body that knocked: the native hands go first."""
    made = make(name, about)
    return FirstHand(made, others, tasks) if name == NATIVE else made
