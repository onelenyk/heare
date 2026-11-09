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


class Handoff(Exception):
    """The work is not this hand's: the brain gives it to another, as worded."""

    def __init__(self, task: str) -> None:
        super().__init__(task)
        self.task = task


class Gone(Exception):
    """The brain no longer knows this job."""


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

    def __init__(self, wire: part.Wire, job: Job,
                 on_permit: Callable[[str, bool | None, str, str], None] | None = None,
                 ) -> None:
        self._wire, self._job = wire, job
        self._on_permit = on_permit
        self.follows, self.asked = job.continues, job.asked
        self.stopped = False
        self._steps: set[asyncio.Task] = set()

    def _path(self, what: str) -> str:
        return f"/api/executor/{self._job.id}/{what}"

    def step(self, text: str) -> None:
        """What it is doing — said without waiting: a step is not an answer."""
        job = asyncio.create_task(self._step(text))
        self._steps.add(job)
        job.add_done_callback(self._steps.discard)

    async def _step(self, text: str) -> None:
        answer = await self._wire.post(self._path("step"), {"text": text})
        if answer.status_code == 404:
            self.stopped = True
            logger.warning("the brain no longer knows job %d; stopping", self._job.id)
            return
        try:
            if answer.json().get("stopped"):
                self.stopped = True
        except ValueError:
            logger.warning("the brain's answer to a step was not JSON")

    async def _call(self, what: str, body: dict) -> dict:
        answer = await self._wire.post(self._path(what), body)
        if answer.status_code == 404:
            raise Gone(f"the brain no longer knows job {self._job.id}")
        found = answer.json()
        if found.get("stopped"):
            raise asyncio.CancelledError
        return found

    async def ask(self, text: str) -> str:
        """The work's question: the person's words come back as the answer."""
        answer = await self._call("ask", {"question": text})
        return str(answer.get("answer") or "")

    async def permit(self, what: str, kind: str = "change", detail: str = "") -> bool:
        weighing = "judge" if kind == "judge" else "person"
        if self._on_permit is not None:
            self._on_permit(what, None, weighing, "")  # the answer may take a while
        answer = await self._call(
            "permit", {"what": what, "kind": kind, "detail": detail})
        allowed = bool(answer.get("allow"))
        if self._on_permit is not None:
            self._on_permit(what, allowed, str(answer.get("by") or "person"),
                            str(answer.get("why") or ""))
        return allowed


async def claim(wire: part.Wire) -> Job | None:
    """One piece of work, or None: nothing waiting, or a brain that said no."""
    answer = await wire.post("/api/executor/claim", {})
    try:
        task = answer.json() if answer.status_code == 200 else {}
    except ValueError:
        return None
    if not isinstance(task, dict) or "id" not in task:
        return None
    return Job(int(task["id"]), str(task.get("request") or ""),
               bool(task.get("continues")), str(task.get("asked") or ""))


async def take(wire: part.Wire,
               do: Callable[[Job, Desk], Awaitable[str]], *,
               on_permit: Callable[[str, bool | None, str, str], None] | None = None,
               on_begin: Callable[[Job, Desk], None] | None = None,
               on_end: Callable[[Job, str, str], None] | None = None,
               failed: Callable[[Exception], str] = str) -> bool:
    """Claim one piece of work and do it; False when none came.

    A stop is ``stopped``; any other failure is ``failed`` with the words
    ``failed`` makes of it — the hand chooses what the person may hear.
    """
    job = await claim(wire)
    if job is None:
        return False
    desk = Desk(wire, job, on_permit)
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
        status, result = "stopped", "Stopped."
    except Handoff as handoff:
        status, result = "handoff", handoff.task
    except Gone as gone:
        logger.warning("job %d: %s", job.id, gone)
        status, result = "stopped", "Stopped."
    except Exception as exc:  # noqa: BLE001 — the brain is told, not crashed
        logger.warning("the work failed: %r", exc)
        status, result = "failed", failed(exc)
    if on_end is not None:
        on_end(job, status, result)
    answer = await wire.post(f"/api/executor/{job.id}/result",
                             {"status": status, "result": result})
    if answer.status_code >= 400:
        logger.warning("the brain refused the result for job %d (HTTP %d)",
                       job.id, answer.status_code)
    return True


async def serve(wire: part.Wire,
                do: Callable[[Job, Desk], Awaitable[str]], **hooks) -> None:
    """Take work forever, resting ``IDLE_S`` whenever none came."""
    while True:
        if not await take(wire, do, **hooks):
            await asyncio.sleep(IDLE_S)
