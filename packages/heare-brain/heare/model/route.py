"""Which hands get a piece of work: a rule, not a model.

The dispatcher asked a model who should do each job — a second or two on
every one, and now and then a guess. The rule is short:

1. hands the person named go there, and nowhere else when they are away;
2. work that goes on from the last goes to the hands that did the last;
3. otherwise the hands on the machine the person spoke on;
4. otherwise the native hands, when they are here, take it first. They call
   the model anyway, so «mine or not» costs nothing: what is not theirs they
   hand on (``Handoff``) as they worded it, to the other hands;
5. otherwise the first hands there are.

The task is the person's words and the last turns of the talk — what the
dispatcher's model used to write out, without the model.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from typing import Any

from heare.core import Executor, Outcome, Task
from heare.core.i18n import _
from heare.model.delegate import Dispatcher
from heare.hand import Handoff

NATIVE = "native-hands"
# How long work waits for native hands that are turned on here but not back
# yet: after a restart the brain is up seconds before its parts are.
WAIT_S = 10.0


def with_talk(request: str, history: list[dict]) -> str:
    talk = "\n".join(f"{m['role']}: {m['content']}" for m in history if m.get("content"))
    return f"{request}\n\nThe last lines of the talk:\n{talk}" if talk else request


def choose(hands: Iterable[Executor]) -> Executor | None:
    """Native hands first, then the lowest id: the answer is the same every time."""
    return min(hands, key=lambda e: (e.name != NATIVE, e.id), default=None)


class Router(Dispatcher):
    """Assigns by the rule; cutting a long result down is still the model's."""

    def __init__(self, settings: Any, tasks: Callable[[], dict[int, Task]],
                 expected: Callable[[], bool] = lambda: False,
                 present: Callable[[], list[Executor]] = list) -> None:
        """expected: whether native hands are turned on here; present: the
        hands there are now, looked at again while waiting for them."""
        super().__init__(settings)
        self._tasks = tasks
        self._expected = expected
        self._present = present

    async def assign(
        self, request: str, history: list[dict], executors: list[Executor]
    ) -> tuple[str, str]:
        task = with_talk(request, history)
        tasks = list(self._tasks().values())
        ids = [e.id for e in executors]
        # The one being assigned is the newest with nobody on it yet.
        current = next((t for t in reversed(tasks) if not t.executor), None)
        if current is not None and current.hand:
            chosen = next((e for e in executors if e.id == current.hand), None)
            if chosen is None:
                chosen = choose(e for e in executors if e.machine == current.hand)
            if chosen is None:
                raise RuntimeError(
                    _("The hands on {where} are not connected.").format(
                        where=current.hand))
            return chosen.id, task
        if current is not None and current.follows:
            last = next((t for t in reversed(tasks) if t.executor in ids), None)
            if last is not None:
                return last.executor, task
        if current is not None and current.origin:
            chosen = choose(e for e in executors if e.machine == current.origin)
            if chosen is not None:
                return chosen.id, task
        if not any(e.name == NATIVE for e in executors) and self._expected():
            executors = await self._wait_for_native(executors)
        chosen = choose(executors)
        if chosen is not None:
            return chosen.id, task
        raise RuntimeError(_("No hands are here."))

    async def _wait_for_native(self, executors: list[Executor]) -> list[Executor]:
        loop = asyncio.get_running_loop()
        end = loop.time() + WAIT_S
        while loop.time() < end:
            now = self._present()
            if any(e.name == NATIVE for e in now):
                return now
            await asyncio.sleep(0.2)
        return executors


class FirstHand:
    """The native hands, and whoever takes what they hand on."""

    def __init__(self, inner: Any, others: Callable[[], list[Executor]],
                 tasks: Callable[[], dict[int, Task]] = dict) -> None:
        self._inner = inner
        self._others = others
        self._tasks = tasks

    @property
    def id(self) -> str:
        return self._inner.id

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def about(self) -> str:
        return self._inner.about

    @property
    def machine(self) -> str:
        return self._inner.machine

    async def run(self, task: str, desk: Any) -> str | Outcome:
        try:
            return await self._inner.run(task, desk)
        except Handoff as handoff:
            current = next(
                (t for t in self._tasks().values()
                 if t.executor == self.id and t.status == "running"
                 and t.request == getattr(desk, "asked", None)),
                None,
            )
            # Only the CLI hands take what the quick hands hand on, never
            # another quick hand and never itself.
            cli = [e for e in self._others() if e is not self and e.name != NATIVE]
            same = [e for e in cli if e.machine == self.machine]
            if same:
                other = min(same, key=lambda e: e.id)
            elif not cli:
                return _("This is not for the quick hands, and no other hands are here.")
            elif current is not None and current.explicit:
                # The person asked for this machine: do not run elsewhere.
                return Outcome("failed", _("No CLI hands on {where}.").format(
                    where=self.machine or "?"))
            else:
                other = min(cli, key=lambda e: e.id)
            # The page shows who does the work: from here on, the other hands.
            if current is not None:
                current.executor = other.id
            desk.step(_("handed over from {name}").format(name=self.name))
            return await other.run(handoff.task, desk)

    def __getattr__(self, name: str) -> Any:
        # The door talks to the body behind it: claim, step, permit, result.
        return getattr(self._inner, name)


def executor(id: str, name: str, about: str, machine: str,
             make: Callable[[str, str, str, str], Any],
             others: Callable[[], list[Executor]],
             tasks: Callable[[], dict[int, Task]] = dict) -> Any:
    """The executor for a body that knocked: the native hands go first."""
    made = make(id, name, about, machine)
    return FirstHand(made, others, tasks) if name == NATIVE else made
