"""The engine: reads what happens, and holds what is worth saying.

A part of the brain, not the whole of it. It does not speak, and does not
decide when to speak — that is D3. Here it does one thing: it listens to
the core's events and writes down what already means something. The first
such event is a piece of work ending.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from heare.core import Message
from heare.core.conversation import RESULT_PREFIX
from heare.core.i18n import _
from heare.engine.intent import ANSWERS, REMINDER, ATTENTION, Intent, ref, tag, tagged
from heare.engine.attention import Attention, Want
from heare.engine.store import Intents
from heare.logs import say

logger = logging.getLogger("heare.engine")

# How often the engine looks at the clock when nothing happens; a reminder
# due while idle should not wait for the next event to be told.
TICK_S = 30.0

# How often the body is written down. Losing a minute of pressure costs
# nothing; writing on every event would cost a file write per event.
ATTENTION_SAVE_S = 60.0
# A plan said out loud is worth holding for days, not forever.
ATTENTION_HOLD_S = 3 * 24 * 3600.0
# It is also not brought back in the same conversation: ten minutes of talk
# is enough for the person to have moved on (10 November).
PLAN_MOMENT_S = 10 * 60.0

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
# A want is worth saying only when it presses harder than this; tiredness
# raises the bar. The number lives here, not in attention: attention says what
# presses, the engine says whether it may be said.
SPEAK_THRESHOLD = 0.50
TIRED_PENALTY = 0.45
# More than three held remarks in one breath is a wall of text, not a remark.
NEWS_PER_BREATH = 3

# What the model reads when attention brings something up. The work-result
# prefix here made her say "the work is done" about a plan (10 November):
# attention carries a thing, it does not finish one.
ON_MIND_PREFIX = (
    "[on your mind] Nothing is finished: this is a thing you have been "
    "carrying. Bring it up in one or two sentences, in the person's language, "
    "as your own thought — not as a report, and do not say it is done. "
    "What presses:"
)

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
                 attention: Attention | None = None,
                 attention_path: Path | None = None,
                 open_loop: Any = None) -> None:
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
        # Without attention the engine reacts to events on the spot. With one it
        # lets a need grow first, so the intent survives the event that made it.
        self._attention_path = Path(attention_path) if attention_path is not None else None
        if attention is None and self._attention_path is not None:
            # A restart wakes the same attention up instead of growing a new one.
            # Before the rename the file was ``soma.json``; read it once, then
            # write the new name, so a live brain keeps its wants.
            path = self._attention_path
            if not path.exists():
                legacy = path.with_name("soma.json")
                if legacy.exists():
                    path = legacy
            attention = Attention.load(path)
        self._attention = attention
        self._attention_at = self._now() if attention is not None else None
        self._attention_saved_at: float | None = None
        self._last_attention_thing: str | None = None
        self._seen_running: set[int] = set()
        self._open_loop = open_loop
        self._blocked = ""
        self._tired = False
        self._spent = False

    def note(self, event: Any) -> None:
        """One event from the core: hold what it means, then deliver what is due."""
        kind = getattr(event, "kind", "")
        # Only the person's own words count. The engine's remark comes back
        # as heard too, and without this it counted as the person answering
        # its own remark: the gap only narrowed, and a want was «carried»
        # before anyone had heard it.
        if kind == "heard" and event.data.get("person", True):
            self._heard_at = self._now()
            self._the_person_spoke()
            self._catch_plan(event)
        elif kind == "task":
            self._hold(event)
        self._deliver_due()
        # What ripens now is told at the next moment, not in the same breath.
        self._advance()

    def _catch_plan(self, event: Any) -> None:
        """A plan said out loud becomes something to hold, not a reply."""
        if self._attention is None or self._open_loop is None:
            return
        phrase = self._open_loop(str(event.data.get("text") or ""))
        if not phrase:
            return
        digest = hashlib.sha1(phrase.lower().encode("utf-8")).hexdigest()[:10]
        self._attention.feed("plan_said", thing_id=f"plan:{digest}", kind="promise", label=phrase)
        say(f"engine: caught a plan {phrase[:60]!r}")

    def _reference(self, data: Any) -> str:
        task_id = int(data.get("id"))
        started = data.get("started")
        if started is None and self._state is not None:
            task = getattr(self._state, "tasks", {}).get(task_id)
            started = getattr(task, "started", None)
        return ref(started, task_id)

    def _hold(self, event: Any) -> None:
        data = event.data
        status = str(data.get("status") or "")
        news = str(data.get("news") or "").strip()
        reference = self._reference(data)
        if self._attention is not None:
            feeling = _FEELING.get(status)
            if feeling:
                label = str(data.get("request") or news)[:200]
                self._attention.feed(feeling, thing_id=reference, kind="task", label=label)
                if status in ("done", "stopped"):
                    self._retire(reference)
        why = _WORTH_TELLING.get(status)
        if why is None or not news:
            return
        self._intents.hold(
            what=news[:1000], why=why, weight=2,
            about=tag("task", reference), until=self._fresh_until(self._now()),
        )
        say(f"engine: held {why} {reference}")

    def _advance(self) -> None:
        """Let the attention feel time pass, and write down what has ripened."""
        if self._attention is None:
            return
        now = self._now()
        if self._attention_at is not None and now > self._attention_at:
            self._attention.tick(now - self._attention_at)
        self._attention_at = now
        # A want the cap just let go takes its remark with it now, not at
        # the next delivery: the three states agree after every step.
        self._forget_released(now)
        self._watch_tasks()
        self._hold_from_attention(now)
        self._save_attention(now)
        self._energy()

    def _retire(self, reference: str) -> None:
        """A closed task takes its waiting remark with it."""
        now = self._now()
        about = tag(ATTENTION, reference)
        for intent in self._intents.alive(now):
            if intent.about == about:
                # Not "delivered": it was never said, and a delivered mark
                # would eat the daily budget and the page's count.
                self._intents.done(intent, status="dropped", at=now)

    def _watch_tasks(self) -> None:
        """A task that is running is a thing left open; the attention keeps it."""
        if self._attention is None or self._state is None:
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
            self._attention.feed(
                "task_opened", thing_id=ref(getattr(task, "started", None), task.id),
                kind="task", label=str(task.request)[:200],
            )

    def _bar(self) -> float:
        """How hard a want must press to be worth saying; tiredness raises it."""
        if self._attention is None:
            return 0.0
        return SPEAK_THRESHOLD + (1.0 - self._attention.energy) * TIRED_PENALTY

    def _ripened(self) -> Want | None:
        want = self._attention.hottest() if self._attention is not None else None
        if want is None or want.pressing < self._bar():
            return None
        return want

    def _hold_from_attention(self, now: float) -> None:
        """Turn what presses into an intent, if it is not held already."""
        want = self._ripened()
        if want is None:
            return
        about = tag(ATTENTION, want.thing_id)
        # Due or still waiting: a plan held until its minute is not due
        # yet, and asking only for due notes held it on every tick.
        if self._intents.waiting(about):
            return
        thing = self._attention.thing(want.thing_id)
        what = thing.label if thing is not None and thing.label else want.thing_id
        weight = 3 if want.pressing >= 0.75 else 2 if want.pressing >= 0.4 else 1
        # A plan was said a breath ago; work failed while the person was
        # away. Only the plan waits for the talk to move on.
        soon = now + PLAN_MOMENT_S if thing is not None and thing.kind == "promise" else now
        self._intents.hold(
            what=what, why=ATTENTION, weight=weight,
            about=about, not_before=soon, until=now + ATTENTION_HOLD_S,
        )
        say(f"engine: held {want.verb} {want.thing_id} pressing {want.pressing:.2f}")

    def _forget_released(self, now: float) -> None:
        """A want attention let go takes its waiting remark with it.

        Without this the cap only moved the backlog from ``attention.json`` into
        ``intents.json``: a released want's remark stayed held for its three
        days and the page showed work that had stopped mattering.
        """
        if self._attention is None:
            return
        for intent in self._intents.alive(now):
            reference = tagged(intent.about, ATTENTION)
            if reference is None:
                continue
            thing = self._attention.thing(reference)
            if thing is None or thing.state != "alive":
                self._intents.done(intent, status="dropped", at=now)

    def _energy(self) -> None:
        """Say once when tiredness starts, and once when it has worn off."""
        if self._attention is None:
            return
        energy = self._attention.energy
        if energy < 0.1 and not self._spent:
            self._spent = self._tired = True
            say("engine: spent; the bar is high")
        elif energy < 0.5 and not self._tired:
            self._tired = True
            say("engine: tired; the bar is higher")
        elif energy >= 0.5:
            self._tired = self._spent = False

    def mood(self) -> str:
        """What presses, in the body's own words; empty when there is no body."""
        return self._attention.state() if self._attention is not None else ""

    def held(self) -> list[Intent]:
        """What waits to be said, heaviest first — the page shows this list."""
        return sorted(
            self._intents.alive(self._now()),
            key=lambda intent: (-intent.weight, intent.made),
        )

    def say_now(self, intent_id: int) -> bool:
        """Say one held intent now, whatever the hour, budget or gap."""
        intent = self._intents.get(intent_id)
        if intent is None or not intent.alive(self._now()):
            return False
        self._deliver([intent])
        return True

    def drop(self, intent_id: int) -> bool:
        """Let one held intent go. A want is treated as heard, so it stops pressing."""
        intent = self._intents.get(intent_id)
        if intent is None or not intent.alive(self._now()):
            return False
        self._intents.done(intent, status="dropped")
        if self._attention is not None and intent.why == ATTENTION:
            reference = tagged(intent.about, ATTENTION)
            if reference is not None:
                # Heard, not spoken: letting one go by hand costs no energy.
                self._attention.spoke(reference, spent=False)
        say(f"engine: dropped {intent_id} by hand")
        return True

    def _save_attention(self, now: float) -> None:
        """Write the body down now and then, not on every event."""
        if self._attention is None or self._attention_path is None:
            return
        if self._attention_saved_at is not None and now - self._attention_saved_at < ATTENTION_SAVE_S:
            return
        self._attention.save(self._attention_path)
        self._attention_saved_at = now

    def _the_person_spoke(self) -> None:
        """Spoke right after a remark of mine: that remark was answered."""
        if self._answered or self._last_spoke is None:
            return
        if self._now() - self._last_spoke <= ANSWERED_S:
            self._answered = True
            self._gap_s = max(MIN_GAP_S, self._gap_s * BOLDER)
            self._ready_at = self._last_spoke + self._gap_s
            if self._attention is not None and self._last_attention_thing is not None:
                self._attention.engage(self._last_attention_thing)
                self._last_attention_thing = None
                say("engine: answered; the gap narrows")

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
        if self._speak is None:
            return
        now = self._now()
        self._forget_released(now)
        self._intents.forget(now)
        held = self._intents.alive(now)
        # The result of work the person asked for is the answer to them, not
        # news they did not ask for: no daily budget, no quiet hours, and no
        # gap since the last remark holds it back (24 October).
        answers = [i for i in held if i.why in ANSWERS]
        if not self._present_and_idle():
            self._silent("away or mid-turn", held)
            return
        due = held if self._waited() else []
        reminders = [i for i in due if i.why == REMINDER]
        news = [i for i in due if i.why not in ANSWERS and i.why != REMINDER]
        if self._is_quiet(now):
            news = []
        elif news:
            news = news[:min(self._news_left(now), NEWS_PER_BREATH)]
        chosen = reminders + answers + news
        if not chosen:
            self._silent(self._why_silent(held, now), held)
            return
        self._blocked = ""
        self._deliver(chosen)

    def _silent(self, reason: str, held: list[Intent]) -> None:
        if not held or not reason or reason == self._blocked:
            return
        self._blocked = reason
        say(f"engine: silent, {reason}")

    def _why_silent(self, held: list[Intent], now: float) -> str:
        if not any(i.why in ANSWERS for i in held) and not self._waited():
            return "too soon after the last remark"
        news = [i for i in held if i.why not in ANSWERS and i.why != REMINDER]
        if news and self._is_quiet(now):
            return "quiet hours"
        if news and self._news_left(now) <= 0:
            return "the daily budget is spent"
        return ""

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
        reminders = [i for i in intents if i.why == REMINDER]
        on_mind = [i for i in intents if i.why == ATTENTION]
        news = [i for i in intents if i.why not in (REMINDER, ATTENTION)]
        if reminders:
            self._speak(Message(
                text=_("Reminder: {what}").format(what="; ".join(i.what for i in reminders)),
                channel="engine", person=False, verbatim=True,
            ))
        if news:
            lines = "\n".join(f"— {i.what}" for i in news)
            self._speak(Message(text=f"{RESULT_PREFIX}\n{lines}",
                                channel="engine", person=False))
        if on_mind:
            lines = "\n".join(f"— {i.what}" for i in on_mind)
            self._speak(Message(text=f"{ON_MIND_PREFIX}\n{lines}",
                                channel="engine", person=False))
        now = self._now()
        if (self._last_spoke is not None and not self._answered
                and now - self._last_spoke > ANSWERED_S):
            # The last remark went unanswered: wait longer before the next.
            self._gap_s = min(MAX_GAP_S, self._gap_s * SHYER)
            if self._attention is not None and self._last_attention_thing is not None:
                self._attention.ignored()
                self._last_attention_thing = None
                say("engine: no answer; the gap widens")
        for intent in intents:
            self._intents.done(intent, at=now)
            if self._attention is not None and intent.why == ATTENTION:
                reference = tagged(intent.about, ATTENTION)
                if reference is not None:
                    self._attention.spoke(reference)
                    self._last_attention_thing = reference
        say(f"engine: said {len(reminders)} reminder(s), {len(news)} news, "
            f"{len(on_mind)} on the mind")
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
