"""A body's hands, reached over the door.

The body is the client: it claims work, runs its agent, reports steps, asks
about changes and returns what came of it. The brain only hands work out and
waits — it no longer holds the agent's process.
"""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass
from typing import Any


@dataclass
class _Job:
    id: int
    request: str
    follows: bool = False
    desk: Any = None
    future: Any = None


class RemoteExecutor:
    """The executor's form, backed by a body that claims work over the wire."""

    def __init__(self, name: str, about: str, *, claim_wait_s: float = 20.0) -> None:
        self.name = name
        self.about = about
        self._wait_s = claim_wait_s
        self._queue: asyncio.Queue[_Job] = asyncio.Queue()
        self._jobs: dict[int, _Job] = {}
        self._ids = itertools.count(1)

    async def run(self, task: str, desk: Any) -> str:
        job = _Job(next(self._ids), task, getattr(desk, "follows", False), desk,
                   asyncio.get_running_loop().create_future())
        self._jobs[job.id] = job
        self._queue.put_nowait(job)
        try:
            return await job.future
        finally:
            # Gone from the table means the next step or permit reads as
            # stopped — which is how a body without a server learns «стоп».
            self._jobs.pop(job.id, None)

    async def claim(self) -> dict | None:
        """Hand one piece of work to the body, or nothing within the wait."""
        try:
            job = await asyncio.wait_for(self._queue.get(), self._wait_s)
        except TimeoutError:
            return None
        return {"id": job.id, "request": job.request, "continues": job.follows}

    def step(self, job_id: int, text: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None:
            return True  # stopped
        job.desk.step(text)
        return False

    async def ask(self, job_id: int, text: str) -> str:
        """An executor's question, waiting for the person's words."""
        job = self._jobs.get(job_id)
        if job is None:
            return ""
        return await job.desk.question(text)

    async def permit(self, job_id: int, what: str, kind: str) -> tuple[bool, bool]:
        job = self._jobs.get(job_id)
        if job is None:
            return False, True  # stopped
        allowed = await job.desk.permit(what, kind)
        return allowed, False

    def result(self, job_id: int, status: str, result: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.future.done():
            return False
        job.future.set_result(result)
        return True
