"""The engine: reads what happens, and holds what is worth saying.

A part of the brain, not the whole of it. It does not speak, and does not
decide when to speak — that is D3. Here it does one thing: it listens to
the core's events and writes down what already means something. The first
such event is a piece of work ending.
"""

from __future__ import annotations

import time
from typing import Any

from heare.engine.store import Intents

# A finished piece of work is worth telling for this long, then it is stale.
WORK_NEWS_S = 2 * 3600

# What a task's end means: done is news, failed is news, stopped is your own
# doing and is not.
_WORTH_TELLING = {"done": "task_done", "failed": "task_failed"}


class Engine:
    """Watches the core's events; holds an intent when one means something."""

    def __init__(self, intents: Intents, *, now=time.time) -> None:
        self._intents = intents
        self._now = now

    def note(self, event: Any) -> None:
        """One event from the core: hold an intent if it means something."""
        if getattr(event, "kind", "") != "task":
            return
        data = event.data
        why = _WORTH_TELLING.get(data.get("status", ""))
        if why is None:
            return
        what = (data.get("result") or data.get("request") or "справа").strip()
        self._intents.hold(
            what=what[:200], why=why, weight=2,
            about=f"task:{data.get('id')}", until=self._now() + WORK_NEWS_S,
        )

    async def run(self, state: Any) -> None:
        """Read the core's events until cancelled."""
        reader = state.subscribe()
        while True:
            self.note(await reader.get())
