"""How a hand lives: claim a piece of work, do it, say what came of it.

Every hand — the agent CLI, the native one — does the same around its work:
asks the brain for a job, gives it a desk to report steps and ask for
leave, sends the result, and asks again. It lived twice, once in each hand,
and its faults lived twice: a hand whose claim came back empty at once asked
again at once, hundreds of times a second, until the brain was back. It
lives here, beside ``heare.part``, and the hands write only what they do.

``take`` is one round; a hand with its own loop (the CLI hand, which stops
to restart its agent) calls it and waits ``IDLE_S`` when nothing came.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from heare import part

logger = logging.getLogger("heare.hand")

# After a claim that brought nothing at once, before the next.
IDLE_S = 1.0


@dataclass(frozen=True)
class Job:
    """One piece of work as the brain hands it out."""

    id: int
    request: str
    continues: bool = False
    asked: str = ""


class Desk:
    """What the work asks the brain for, over the door.

    A step's answer says whether the work was called off: that is how
    «стоп» reaches a hand with no server of its own. ``stopped`` is read
    before the next act, so nothing is done after a stop.
    """

    def __init__(self, wire: part.Wire, job: Job, name: str,
                 on_permit: Callable[[str, bool | None, str, str], None] | None = None,
                 ) -> None:
        self._wire, self._job, self._name = wire, job, name
        self._on_permit = on_permit
        self.follows, self.asked = job.continues, job.asked
        self.stopped = False
        self._steps: set[asyncio.Task] = set()

    def _path(self, what: str) -> str:
        return f"/api/executor/{self._job.id}/{what}?name={self._name}"

    def step(self, text: str) -> None:
        """What it is doing — said without waiting: a step is not an answer."""
        job = asyncio.create_task(self._step(text))
        self._steps.add(job)
        job.add_done_callback(self._steps.discard)

    async def _step(self, text: str) -> None:
        answer = await self._wire.post(self._path("step"), {"text": text})
        try:
            if answer.json().get("stopped"):
                self.stopped = True
        except ValueError:
            pass

    async def ask(self, text: str) -> str:
        """The work's question: the person's words come back as the answer."""
        answer = (await self._wire.post(self._path("ask"), {"question": text})).json()
        if answer.get("stopped"):
            raise asyncio.CancelledError
        return str(answer.get("answer") or "")

    async def permit(self, what: str, kind: str = "change", detail: str = "") -> bool:
        weighing = "judge" if kind == "judge" else "person"
        if self._on_permit is not None:
            self._on_permit(what, None, weighing, "")  # the answer may take a while
        answer = (await self._wire.post(
            self._path("permit"), {"what": what, "kind": kind, "detail": detail})).json()
        if answer.get("stopped"):
            raise asyncio.CancelledError
        allowed = bool(answer.get("allow"))
        if self._on_permit is not None:
            self._on_permit(what, allowed, str(answer.get("by") or "person"),
                            str(answer.get("why") or ""))
        return allowed


async def claim(wire: part.Wire, name: str) -> Job | None:
    """One piece of work, or None: nothing waiting, or a brain that said no."""
    answer = await wire.post("/api/executor/claim", {"name": name})
    try:
        task = answer.json() if answer.status_code == 200 else {}
    except ValueError:
        return None
    if not isinstance(task, dict) or "id" not in task:
        return None
    return Job(int(task["id"]), str(task.get("request") or ""),
               bool(task.get("continues")), str(task.get("asked") or ""))


async def take(wire: part.Wire, name: str,
               do: Callable[[Job, Desk], Awaitable[str]], *,
               on_permit: Callable[[str, bool | None, str, str], None] | None = None,
               on_begin: Callable[[Job, Desk], None] | None = None,
               on_end: Callable[[Job, str, str], None] | None = None,
               failed: Callable[[Exception], str] = str) -> bool:
    """Claim one piece of work and do it; False when none came.

    A stop is ``stopped``; any other failure is ``failed`` with the words
    ``failed`` makes of it — the hand chooses what the person may hear.
    """
    job = await claim(wire, name)
    if job is None:
        return False
    desk = Desk(wire, job, name, on_permit)
    if on_begin is not None:
        on_begin(job, desk)
    status, result = "done", ""
    try:
        result = await do(job, desk)
    except asyncio.CancelledError:
        # The hand itself being shut down goes on out; only the work being
        # called off (a stop from the brain) is a result.
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise
        status, result = "stopped", "Зупинено."
    except Exception as exc:  # noqa: BLE001 — the brain is told, not crashed
        logger.warning("the work failed: %r", exc)
        status, result = "failed", failed(exc)
    if on_end is not None:
        on_end(job, status, result)
    await wire.post(f"/api/executor/{job.id}/result?name={name}",
                    {"status": status, "result": result})
    return True


async def serve(wire: part.Wire, name: str,
                do: Callable[[Job, Desk], Awaitable[str]], **hooks) -> None:
    """Take work forever, resting ``IDLE_S`` whenever none came."""
    while True:
        if not await take(wire, name, do, **hooks):
            await asyncio.sleep(IDLE_S)
