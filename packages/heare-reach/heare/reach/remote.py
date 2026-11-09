"""A body's hands, reached over the door.

The body is the client: it claims work, runs its agent, reports steps, asks
about changes and returns what came of it. The brain only hands work out and
waits — it no longer holds the agent's process.
"""

from __future__ import annotations

import asyncio
import itertools
from collections import deque
from dataclasses import dataclass
from typing import Any

from heare.core import Outcome
from heare.core.i18n import _
from heare.hand import Gone, Handoff

# How long a hand that went away keeps its queued work: a stream that dropped
# for a moment is not a hand that left, and replaying it is worse than waiting.
AWAY_GRACE_S = 60.0


@dataclass
class _Job:
    id: int
    request: str
    follows: bool = False
    asked: str = ""
    desk: Any = None
    future: Any = None


class RemoteExecutor:
    """The executor's form, backed by a body that claims work over the wire."""

    def __init__(self, id: str, name: str, about: str, machine: str = "",
                 *, claim_wait_s: float = 20.0) -> None:
        self.id = id
        self.name = name
        self.about = about
        self.machine = machine
        self._wait_s = claim_wait_s
        self._queue: deque[_Job] = deque()
        self._ready = asyncio.Event()
        self._jobs: dict[int, _Job] = {}
        self._ids = itertools.count(1)
        self.present = True
        self._away: asyncio.TimerHandle | None = None

    def set_present(self, on: bool) -> None:
        """The door says whether the body's stream is here. An away hand
        keeps its queued work for the grace, then the work fails."""
        if on:
            if self._away is not None:
                self._away.cancel()
                self._away = None
            self.present = True
            return
        if not self.present:
            return
        self.present = False
        if self._jobs and self._away is None:
            self._away = asyncio.get_running_loop().call_later(
                AWAY_GRACE_S, self._fail_jobs)

    def _fail_jobs(self) -> None:
        if self._away is not None:
            self._away.cancel()
            self._away = None
        text = _("The hands on {where} are not connected.").format(
            where=self.machine or "?")
        for job in self._jobs.values():
            if not job.future.done():
                job.future.set_result(Outcome("failed", text))
        self._queue.clear()

    def close(self) -> None:
        self._fail_jobs()

    async def run(self, task: str, desk: Any) -> str | Outcome:
        if not self.present:
            return Outcome("failed", _(
                "The hands on {where} are not connected.").format(
                    where=self.machine or "?"))
        job = _Job(next(self._ids), task, getattr(desk, "follows", False),
                   getattr(desk, "asked", ""), desk,
                   asyncio.get_running_loop().create_future())
        self._jobs[job.id] = job
        self._queue.append(job)
        self._ready.set()
        try:
            return await job.future
        finally:
            # Gone from the table means the next step or permit reads as
            # stopped — which is how a body without a server learns «стоп».
            self._jobs.pop(job.id, None)
            # A job nobody took ends here too: it must never be handed out
            # a second time after its future died (the core's own timeout).
            try:
                self._queue.remove(job)
            except ValueError:
                pass

    async def claim(self) -> dict | None:
        """Hand one piece of work to the body, or nothing within the wait."""
        while True:
            while self._queue:
                job = self._queue.popleft()
                if job.future.done():
                    self._jobs.pop(job.id, None)
                    continue
                return {"id": job.id, "request": job.request,
                        "continues": job.follows, "asked": job.asked}
            self._ready.clear()
            if self._queue:
                continue
            try:
                await asyncio.wait_for(self._ready.wait(), self._wait_s)
            except TimeoutError:
                return None

    def _over(self, job: _Job | None) -> bool:
        """The job is gone, or its future already ended: either way stopped."""
        return job is None or job.future.done()

    def step(self, job_id: int, text: str) -> bool:
        job = self._jobs.get(job_id)
        if self._over(job):
            return True  # stopped
        assert job is not None
        job.desk.step(text)
        return False

    async def ask(self, job_id: int, text: str) -> str:
        """An executor's question, waiting for the person's words."""
        job = self._jobs.get(job_id)
        if self._over(job):
            raise Gone(f"the brain no longer knows job {job_id}")
        assert job is not None
        return await job.desk.question(text)

    async def permit(self, job_id: int, what: str, kind: str,
                     detail: str = "") -> tuple[bool, bool]:
        job = self._jobs.get(job_id)
        if self._over(job):
            return False, True  # stopped
        assert job is not None
        allowed = await job.desk.permit(what, kind, detail)
        return allowed, False

    def verdict(self, job_id: int) -> tuple[str, str]:
        """Who allowed the job's last action, and why: the gate or the person."""
        job = self._jobs.get(job_id)
        found = getattr(job.desk, "verdict", None) if job is not None else None
        if isinstance(found, tuple) and len(found) == 2:
            return str(found[0]), str(found[1])
        return ("person", "")

    def result(self, job_id: int, status: str, result: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.future.done():
            return False
        if status == "handoff":
            # Not this body's work: whoever routes it hands it on, as worded.
            job.future.set_exception(Handoff(result))
        elif status in ("failed", "stopped"):
            job.future.set_result(Outcome(status, result))
        else:
            job.future.set_result(result)
        return True
