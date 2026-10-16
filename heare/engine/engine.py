"""The engine: reads what happens, and holds what is worth saying.

A part of the brain, not the whole of it. It does not speak, and does not
decide when to speak — that is D3. Here it does one thing: it listens to
the core's events and writes down what already means something. The first
such event is a piece of work ending.
"""

from __future__ import annotations

import time
from typing import Any

from heare.core import Message
from heare.core.conversation import RESULT_PREFIX
from heare.engine.intent import Intent
from heare.engine.store import Intents

# A finished piece of work is worth telling for this long, then it is stale.
WORK_NEWS_S = 2 * 3600

# What a task's end means: done is news, failed is news, stopped is your own
# doing and is not.
_WORTH_TELLING = {"done": "task_done", "failed": "task_failed"}


class Engine:
    """Watches the core's events; holds an intent when one means something."""

    def __init__(self, intents: Intents, *, state: Any = None,
                 speak: Any = None, now=time.time) -> None:
        self._intents = intents
        self._state = state
        self._speak = speak
        self._now = now

    def note(self, event: Any) -> None:
        """One event from the core: hold what it means, then deliver what is due."""
        if getattr(event, "kind", "") != "task":
            self._deliver_due()
            return
        data = event.data
        why = _WORTH_TELLING.get(data.get("status", ""))
        news = str(data.get("news") or "").strip()
        if why is not None and news:
            self._intents.hold(
                what=news[:200], why=why, weight=2,
                about=f"task:{data.get('id')}", until=self._now() + WORK_NEWS_S,
            )
        self._deliver_due()

    def _deliver_due(self) -> None:
        """Deliver what is held — but only at a moment that suits."""
        if self._speak is None or not self._good_moment():
            return
        for intent in self._intents.alive(self._now()):
            self._deliver(intent)

    def _good_moment(self) -> bool:
        """Present, and not mid-turn: away means it waits.

        This is the first cell of the decision table in docs/engine.md §3.5.
        The rest — music, a late hour, the three ways — grows from here (D4).
        """
        if self._state is None:
            return True
        if not getattr(self._state, "around", {}).get("present", True):
            return False
        return getattr(self._state, "phase", "idle") == "idle"

    def _deliver(self, intent: Intent) -> None:
        """Hand it to the conversation; the core says it at a quiet moment."""
        self._speak(Message(text=f"{RESULT_PREFIX}{intent.what}",
                            channel="engine", person=False))
        self._intents.done(intent)

    async def run(self) -> None:
        """Read the core's events until cancelled."""
        if self._state is None:
            return
        reader = self._state.subscribe()
        while True:
            self.note(await reader.get())
