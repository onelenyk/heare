"""The engine: reads what happens, and holds what is worth saying.

A part of the brain, not the whole of it. It does not speak, and does not
decide when to speak — that is D3. Here it does one thing: it listens to
the core's events and writes down what already means something. The first
such event is a piece of work ending.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from typing import Any

from heare.core import Message
from heare.core.conversation import RESULT_PREFIX
from heare.engine.intent import Intent
from heare.engine.store import Intents

# How often the engine looks at the clock when nothing happens; a reminder
# due while idle should not wait for the next event to be told.
TICK_S = 30.0

# A finished piece of work is worth telling for this long, then it is stale.
WORK_NEWS_S = 2 * 3600

# What a task's end means: done is news, failed is news, stopped is your own
# doing and is not.
_WORTH_TELLING = {"done": "task_done", "failed": "task_failed"}

# The least it says two things in a row. It grows bolder when the person
# answers and waits longer when nobody does — trust, earned and lost.
BASE_GAP_S = 4 * 60.0
MIN_GAP_S = 60.0
MAX_GAP_S = 30 * 60.0
# A reply within this long of a remark counts as the person listening to it.
ANSWERED_S = 90.0
BOLDER = 0.6
SHYER = 1.5


def _minutes(hhmm: str) -> int | None:
    """A local ``HH:MM`` as minutes past midnight; None if it is not one."""
    try:
        hours, minutes = (int(part) for part in str(hhmm).split(":", 1))
    except ValueError:
        return None
    if not (0 <= hours < 24 and 0 <= minutes < 60):
        return None
    return hours * 60 + minutes


class Engine:
    """Watches the core's events; holds intents, and tells them at a good moment."""

    def __init__(self, intents: Intents, *, state: Any = None,
                 speak: Any = None, gap: float = BASE_GAP_S,
                 tick: float = TICK_S, now=time.time,
                 quiet: tuple[str, str] = ("22:00", "08:00"),
                 daily_limit: int = 1) -> None:
        self._intents = intents
        self._state = state
        self._speak = speak
        self._now = now
        self._tick = tick
        self._gap_s = gap
        self._ready_at: float | None = None
        self._last_spoke: float | None = None
        self._answered = True
        self._quiet_from = _minutes(quiet[0])
        self._quiet_to = _minutes(quiet[1])
        if self._quiet_from == self._quiet_to:
            # Equal ends are not a window, not a day-long silence.
            self._quiet_from = self._quiet_to = None
        self._daily_limit = max(0, int(daily_limit))

    def note(self, event: Any) -> None:
        """One event from the core: hold what it means, then deliver what is due."""
        kind = getattr(event, "kind", "")
        if kind == "heard":
            self._the_person_spoke()
        elif kind == "task":
            self._hold(event)
        self._deliver_due()

    def _hold(self, event: Any) -> None:
        data = event.data
        why = _WORTH_TELLING.get(data.get("status", ""))
        news = str(data.get("news") or "").strip()
        if why is None or not news:
            return
        self._intents.hold(
            what=news[:200], why=why, weight=2,
            about=f"task:{data.get('id')}", until=self._fresh_until(self._now()),
        )

    def _the_person_spoke(self) -> None:
        """Spoke right after a remark of mine: that remark was answered."""
        if self._answered or self._last_spoke is None:
            return
        if self._now() - self._last_spoke <= ANSWERED_S:
            self._answered = True
            self._gap_s = max(MIN_GAP_S, self._gap_s * BOLDER)
            self._ready_at = self._last_spoke + self._gap_s

    def _fresh_until(self, now: float) -> float:
        """How long work news stays worth telling.

        Quiet hours must not quietly eat it, so news that would go stale while
        the window is on keeps its full two hours after the window ends.
        """
        until = now + WORK_NEWS_S
        if self._is_quiet(until):
            until = self._quiet_ends(until) + WORK_NEWS_S
        return until

    def _is_quiet(self, when: float) -> bool:
        if self._quiet_from is None or self._quiet_to is None:
            return False
        moment = datetime.fromtimestamp(when)
        minute = moment.hour * 60 + moment.minute
        if self._quiet_from < self._quiet_to:
            return self._quiet_from <= minute < self._quiet_to
        return minute >= self._quiet_from or minute < self._quiet_to

    def _quiet_ends(self, when: float) -> float:
        """The next close of the quiet window, for a moment inside it."""
        if self._quiet_to is None:
            return when
        moment = datetime.fromtimestamp(when)
        end = moment.replace(
            hour=self._quiet_to // 60, minute=self._quiet_to % 60,
            second=0, microsecond=0,
        )
        if end <= moment:
            end += timedelta(days=1)
        return end.timestamp()

    def _news_left(self, now: float) -> int:
        return max(0, self._daily_limit - self._intents.news_delivered(now))

    def _deliver_due(self) -> None:
        """Deliver what is held — all of it in one breath — at a moment that suits."""
        if self._speak is None or not self._good_moment():
            return
        now = self._now()
        due = self._intents.alive(now)
        if not due:
            return
        reminders = [i for i in due if i.why == "reminder"]
        news = [i for i in due if i.why != "reminder"]
        if self._is_quiet(now):
            news = []
        elif news:
            news = news[:self._news_left(now)]
        chosen = reminders + news
        if not chosen:
            return
        self._deliver(chosen)

    def _good_moment(self) -> bool:
        """Not too soon, present, and not mid-turn.

        This is the decision table of docs/engine.md §3.5 as far as it goes:
        away means it waits, mid-turn means it waits, and the gap since the
        last remark widens when remarks go unanswered. The ways — writing
        instead of saying — need a second body, and come later.
        """
        if self._ready_at is not None and self._now() < self._ready_at:
            return False
        if self._state is None:
            return True
        if not getattr(self._state, "around", {}).get("present", True):
            return False
        return getattr(self._state, "phase", "idle") == "idle"

    def _deliver(self, intents: list[Intent]) -> None:
        """Reminders as written; work news as the model's own rephrase.

        Three pieces of work finished while you were away are one remark,
        not three — told apart in one breath instead of minutes apart. A
        reminder is exact words and costs no model call at all.
        """
        reminders = [i for i in intents if i.why == "reminder"]
        news = [i for i in intents if i.why != "reminder"]
        if reminders:
            self._speak(Message(
                text="Нагадування: " + "; ".join(i.what for i in reminders),
                channel="engine", person=False, verbatim=True,
            ))
        if news:
            lines = "\n".join(f"— {i.what}" for i in news)
            self._speak(Message(text=f"{RESULT_PREFIX}\n{lines}",
                                channel="engine", person=False))
        now = self._now()
        if (self._last_spoke is not None and not self._answered
                and now - self._last_spoke > ANSWERED_S):
            # The last remark went unanswered: wait longer before the next.
            self._gap_s = min(MAX_GAP_S, self._gap_s * SHYER)
        for intent in intents:
            self._intents.done(intent, at=now)
        self._last_spoke = now
        self._ready_at = now + self._gap_s
        self._answered = False

    async def run(self) -> None:
        """Read the core's events until cancelled, and look at the clock
        even when no event comes."""
        if self._state is None:
            return
        reader = self._state.subscribe()
        while True:
            try:
                event = await asyncio.wait_for(reader.get(), timeout=self._tick)
            except TimeoutError:
                self._deliver_due()
                continue
            self.note(event)
