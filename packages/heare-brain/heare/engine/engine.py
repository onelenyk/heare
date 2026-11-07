"""The engine: reads what happens, and holds what is worth saying.

A part of the brain, not the whole of it. It does not speak, and does not
decide when to speak — that is D3. Here it does one thing: it listens to
the core's events and writes down what already means something. The first
such event is a piece of work ending.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from heare.core import Message
from heare.core.conversation import RESULT_PREFIX
from heare.engine.intent import Intent
from heare.engine.soma import Body
from heare.engine.store import Intents
from heare.core.i18n import _

logger = logging.getLogger("heare.engine")

# How often the engine looks at the clock when nothing happens; a reminder
# due while idle should not wait for the next event to be told.
TICK_S = 30.0

# How often the body is written down. Losing a minute of pressure costs
# nothing; writing on every event would cost a file write per event.
BODY_SAVE_S = 60.0

# A finished piece of work is worth telling for this long, then it is stale.
WORK_NEWS_S = 2 * 3600

# What a task's end means: done is news, failed is news, stopped is your own
# doing and is not.
_WORTH_TELLING = {"done": "task_done", "failed": "task_failed"}
# What a task's state does to the body. ``running`` is not an event the core
# sends — it is read from the snapshot, because a thing that started and has
# not ended is exactly what a body is for.
_FEELING = {
    "running": "task_opened",
    "done": "task_done",
    "failed": "task_failed",
    "stopped": "task_done",
}
# The result of work the person asked for is the answer to them, not news
# they did not ask for: no daily budget, no quiet hours, no gap since the
# last remark holds it back. With a budget of one a day, every result after
# the first was never said (24 October).
_ANSWERS = frozenset(_WORTH_TELLING.values())

# The least it says two things in a row. It grows bolder when the person
# answers and waits longer when nobody does — trust, earned and lost.
BASE_GAP_S = 4 * 60.0
MIN_GAP_S = 60.0
MAX_GAP_S = 30 * 60.0
# A reply within this long of a remark counts as the person listening to it.
ANSWERED_S = 90.0
# Who spoke or typed this recently is there, whatever the eyes say: a result
# held «away» while the person was typing to it was never said (24 October).
SPOKE_S = 10 * 60.0
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
                 daily_limit: int = 1,
                 body: Body | None = None,
                 body_path: Path | None = None) -> None:
        self._intents = intents
        self._state = state
        self._speak = speak
        self._now = now
        self._tick = tick
        self._gap_s = gap
        self._ready_at: float | None = None
        self._last_spoke: float | None = None
        self._heard_at: float | None = None
        self._answered = True
        self._quiet_from = _minutes(quiet[0])
        self._quiet_to = _minutes(quiet[1])
        if self._quiet_from == self._quiet_to:
            # Equal ends are not a window, not a day-long silence.
            self._quiet_from = self._quiet_to = None
        self._daily_limit = max(0, int(daily_limit))
        # Without a body the engine reacts to events on the spot. With one it
        # lets a need grow first, so the intent survives the event that made it.
        self._body_path = Path(body_path) if body_path is not None else None
        if body is None and self._body_path is not None:
            # A restart wakes the same body up instead of growing a new one.
            body = Body.load(self._body_path)
        self._body = body
        self._body_at = self._now() if body is not None else None
        self._body_saved_at: float | None = None
        self._seen_running: set[int] = set()

    def note(self, event: Any) -> None:
        """One event from the core: hold what it means, then deliver what is due."""
        kind = getattr(event, "kind", "")
        if kind == "heard":
            self._heard_at = self._now()
            self._the_person_spoke()
        elif kind == "task":
            self._hold(event)
        self._deliver_due()
        # What ripens now is told at the next moment, not in the same breath.
        self._advance()

    def _hold(self, event: Any) -> None:
        data = event.data
        status = str(data.get("status") or "")
        news = str(data.get("news") or "").strip()
        # The body hears every ending, including one that carries no news.
        if self._body is not None:
            feeling = _FEELING.get(status)
            if feeling:
                label = str(data.get("request") or news)[:200]
                self._body.feed(feeling, about=f"task:{data.get('id')}", label=label)
                if status in ("done", "stopped"):
                    self._retire(label)
        why = _WORTH_TELLING.get(status)
        if why is None or not news:
            return
        self._intents.hold(
            what=news[:1000], why=why, weight=2,
            about=f"task:{data.get('id')}", until=self._fresh_until(self._now()),
        )

    def _advance(self) -> None:
        """Let the body feel time pass, and write down what has ripened."""
        if self._body is None:
            return
        now = self._now()
        if self._body_at is not None and now > self._body_at:
            self._body.tick(now - self._body_at)
        self._body_at = now
        self._watch_tasks()
        self._hold_from_body(now)
        self._save_body(now)

    def _retire(self, label: str) -> None:
        """A closed task takes its waiting remark with it."""
        now = self._now()
        about = "soma:" + label
        for intent in self._intents.alive(now):
            if intent.about == about:
                self._intents.done(intent, at=now)

    def _watch_tasks(self) -> None:
        """A task that is running is a thing left open; the body keeps it."""
        if self._body is None or self._state is None:
            return
        tasks = getattr(self._state, "tasks", None)
        if not isinstance(tasks, dict):
            return
        for task in tasks.values():
            if getattr(task, "status", "") != "running":
                continue
            if task.id in self._seen_running:
                continue
            self._seen_running.add(task.id)
            self._body.feed(
                "task_opened", about=f"task:{task.id}", label=str(task.request)[:200],
            )

    def _hold_from_body(self, now: float) -> None:
        """Turn what has ripened into an intent, if it is not held already."""
        held = self._body.propose() if self._body is not None else None
        if held is None:
            return
        about = "soma:" + held.about
        if any(intent.about == about for intent in self._intents.alive(now)):
            return
        weight = 3 if held.commitment >= 0.75 else 2 if held.commitment >= 0.4 else 1
        # The body counts its own seconds; the store counts wall time.
        left = held.until - self._body.t if held.until is not None else None
        self._intents.hold(
            what=held.about, why="soma", weight=weight,
            about=about, until=(now + left) if left is not None else None,
            not_before=now,
        )

    def mood(self) -> str:
        """What presses, in the body's own words; empty when there is no body."""
        return self._body.state() if self._body is not None else ""

    def _save_body(self, now: float) -> None:
        """Write the body down now and then, not on every event."""
        if self._body is None or self._body_path is None:
            return
        if self._body_saved_at is not None and now - self._body_saved_at < BODY_SAVE_S:
            return
        self._body.save(self._body_path)
        self._body_saved_at = now

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
        if self._speak is None or not self._present_and_idle():
            return
        now = self._now()
        due = self._intents.alive(now)
        answers = [i for i in due if i.why in _ANSWERS]
        if not self._waited():
            due = []
        reminders = [i for i in due if i.why == "reminder"]
        news = [i for i in due if i.why not in _ANSWERS and i.why != "reminder"]
        if self._is_quiet(now):
            news = []
        elif news:
            news = news[:self._news_left(now)]
        chosen = reminders + answers + news
        if not chosen:
            return
        self._deliver(chosen)

    def _waited(self) -> bool:
        """Not too soon since the last remark: the gap widens when remarks
        go unanswered (docs/engine.md §3.5). Answers do not wait for it."""
        return self._ready_at is None or self._now() >= self._ready_at

    def _present_and_idle(self) -> bool:
        """Away means it waits, mid-turn means it waits. The ways — writing
        instead of saying — need a second body, and come later."""
        if self._state is None:
            return True
        spoke = self._heard_at is not None and self._now() - self._heard_at <= SPOKE_S
        if not spoke:
            present = self._state.around_any("present")
            if present is not None and not present:
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
                text=_("Reminder: {what}").format(what="; ".join(i.what for i in reminders)),
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
                try:
                    event = await asyncio.wait_for(reader.get(), timeout=self._tick)
                except TimeoutError:
                    self._deliver_due()
                    self._advance()
                    continue
                self.note(event)
            except Exception:
                # A file that cannot be written, or a row that cannot be read,
                # must not end the engine: the next event or tick tries again.
                logger.exception("the engine could not keep an intent")
