"""The engine — the thing above the conductor that holds something.

``loop.py`` conducts a turn: sound in, recognition, model, speech. It does
that well and it wants nothing, because a turn is all it has. So between
one utterance and the next there was nobody home — no position, no
outstanding business, nothing accumulating. That absence is what makes an
assistant feel like a function rather than someone: it can be correct
forever without ever being present.

The engine ticks beside the conversation. Three steps, in order:

    notice   →  turn what happened into intents worth raising
    judge    →  is there cause to speak now, and which one
    speak    →  through loop.inject(), so the model phrases it in voice

Where the model is allowed
--------------------------
Almost nowhere. Everything that can be a condition is a condition: are
you talking, is anything pending, was this said recently, has it been
forward too often already. The model is asked exactly one question, only
after the conditions have let something through — is this worth saying
now, and how to put it in a sentence.

That division was earned the hard way. Rules written as prose in a prompt
are followed sometimes; rules written as code hold every time.

Why "freely" is survivable
--------------------------
It may speak unbidden with no fixed quota, which is the riskiest setting
there is: an assistant that is slow gets tolerated, an assistant that
intrudes gets switched off. The safeguard is not a table of limits but
consequence. Every unbidden remark is watched: answered on the subject
and it grows a little bolder, talked past or waved away and it goes
quiet for longer. Trust is a multiplier on its own patience, and it is
the only reason "freely" does not become "constantly" inside a day.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from src.spine import intents as I
from src.spine.situation import Situation, observe

logger = logging.getLogger("spine.engine")

TICK_S = 5.0

# How often to read the surroundings. Slower than the tick — the room
# does not change every five seconds, and the judge still runs at full
# rate so a ripe intent is not left waiting.
WATCH_EVERY_S = 20.0

# How long it waits between unbidden remarks at full trust. Multiplied by
# trust, which climbs when it is brushed off.
BASE_QUIET_S = 15 * 60.0

# How long a conversation has to stay quiet before it counts as over.
# A voice assistant has no hang-up, so silence is the only boundary
# there is — and it has to be long enough that going to look something
# up, or thinking, does not end anything.
CONVERSATION_IDLE_S = 30 * 60.0

# How often to ask whether it has. One indexed query, but there is no
# reason to run it twelve times a minute.
CLOSE_EVERY_S = 60.0

# How long a line said near it, but not to it, is kept. This number is
# the difference between working memory and a recording someone forgot
# they were making: long enough to search back over the week you are in,
# short enough that nothing surfaces from a room you have forgotten.
# Addressed speech is never touched by this.
OVERHEARD_KEEP_S = 7 * 24 * 3600.0

# Once an hour is often enough to take out the rubbish.
FORGET_EVERY_S = 3600.0

# How pressing a repeated intention is. Low, and that is the point: it
# is the least urgent thing the engine can hold, so it never outranks
# "the thing you asked for finished", and at night it is filtered out
# entirely — `judge` keeps only what was asked for and is urgent.
#
# It is given no expiry, unlike a remark about what you are doing now.
# What you keep meaning to do does not stop being true while it waits,
# and while it waits it sits in the prompt, which is half of what it is
# for: the next time you open a conversation, the assistant already
# knows what is outstanding between you.
REPEAT_URGENCY = 0.3

# On the first pass after a start, how recently a finished job must have
# finished to still be worth mentioning. Wide enough that work completed
# just before a restart is not lost, narrow enough that yesterday stays
# where it belongs.
STALE_JOB_S = 10 * 60.0

# Trust bounds. At 1.0 it speaks after the base wait; at 8.0 it is
# effectively holding its tongue for two hours at a time.
TRUST_MIN = 1.0
TRUST_MAX = 8.0
TRUST_UP = 1.8  # ignored / rejected: back off, fast
TRUST_DOWN = 0.7  # accepted: come back sooner

# Being waved away is not the same as being missed. One is a decision.
BRUSH_OFF = (
    "не зараз",
    "потім",
    "помовч",
    "тихо",
    "дай спокій",
    "відчепись",
    "не треба",
)


@dataclass
class Verdict:
    """Why it did or did not speak. Every path returns one, so a silent
    engine can be asked what it was thinking — the failure mode of
    proactive systems is silence you cannot interrogate."""

    speak: bool
    reason: str
    intent: I.Intent | None = None


@dataclass
class EngineState:
    trust: float = TRUST_MIN
    unprompted_last_ts: float = 0.0
    unprompted_times: list[float] = field(default_factory=list)


def judge(
    situation: Situation, pending: list[I.Intent], state: EngineState
) -> Verdict:
    """Should it say something now — and which thing?

    A pure function of the present. No clock, no database, no network:
    everything it needs arrives in the arguments. That is what makes the
    boundaries testable — "quiet at night", "never mid-turn", "quieter
    after being brushed off" become a table of cases instead of an
    afternoon of listening.
    """
    if not pending:
        return Verdict(False, "нема про що")

    # Mid-turn, anything said is an interruption rather than a remark.
    if situation.busy_talking:
        return Verdict(False, "розмова триває")

    # Nobody to hear it. Speaking to an empty room spends the intent and
    # buys nothing — better to keep it for when they are back.
    if not situation.user_is_here:
        return Verdict(False, "нікого немає")

    ripe = [i for i in pending if i.ripe(situation.now)]
    if not ripe:
        return Verdict(False, "ще не на часі")

    # Night. Only what was asked for, and only if it is urgent — its own
    # noticings can always wait until morning.
    if situation.is_night:
        ripe = [i for i in ripe if i.origin == I.USER and i.urgency >= 0.8]
        if not ripe:
            return Verdict(False, "ніч")

    # Let the person finish their thought. A remark landing on the tail
    # of their sentence reads as interrupting even when the microphone
    # says they stopped.
    if situation.user_silence_s < 8:
        return Verdict(False, "щойно говорив")

    wait = BASE_QUIET_S * state.trust
    top = max(ripe, key=lambda i: i.urgency)

    # Urgency buys impatience, but never the whole wait.
    if situation.unprompted_last_s < wait * (1.0 - 0.5 * top.urgency):
        return Verdict(False, f"зарано, ще {int(wait / 60)} хв")

    return Verdict(True, "є привід", top)


def reaction_to(text: str, spoke_about: I.Intent | None) -> str | None:
    """How the last unbidden remark landed, from what was said next.

    Deliberately crude. Two things are unmistakable — being waved away,
    and being answered on the subject — and everything between them is
    read as having been talked past. Guessing finer than the signal would
    make the engine's confidence a fiction.
    """
    if spoke_about is None:
        return None
    lowered = (text or "").strip().lower()
    if not lowered:
        return None
    if any(phrase in lowered for phrase in BRUSH_OFF):
        return I.REJECTED
    words = {w.strip(".,!?—«»").lower() for w in spoke_about.text.split()}
    said = {w.strip(".,!?—«»").lower() for w in lowered.split()}
    overlap = len(words & said) / max(1, min(len(words), len(said)))
    return I.ACCEPTED if overlap > 0.25 else I.IGNORED


class Engine:
    """Ticks beside the conversation and holds what outlives a turn.

    Collaborators are injected, never imported: the conductor owns the
    mouth, the stores own their tables, and this owns only the decision.
    Nothing here touches a device or a socket, so the whole thing runs
    under a test with a fake clock.
    """

    def __init__(
        self,
        *,
        store: Any,
        say: Any,
        state: Any = None,
        persist: Any = None,
        jobs: Any = None,
        ask: Any = None,
        idle: Any = None,
        summarise: Any = None,
        watch: Any = None,
        repeats: Any = None,
        tick_s: float = TICK_S,
        watch_every_s: float = WATCH_EVERY_S,
    ) -> None:
        self._store = store
        self._say = say  # async (text) -> None — normally loop.inject
        self._state = state
        self._persist = persist
        self._jobs = jobs
        self._ask = ask  # async (intent, situation) -> str | None
        # async () -> seconds since the keyboard was last touched. Not
        # part of the watcher: "is anyone there" is what stops it
        # speaking to an empty room, and it has to work with the watcher
        # switched off.
        self._idle = idle
        # An EnvironmentWatch, or nothing. Absent, the engine behaves
        # exactly as before: it only ever reports on itself.
        self._watch = watch
        # async (said) -> str | None. Absent, conversations still close;
        # they just close without a summary, which is the honest state
        # for a machine with no model wired.
        self._summarise = summarise
        # A `Repeats`, or nothing. It answers one question — is the
        # person saying they mean to do the same thing over and over —
        # and it is asked only when a conversation ends, never on a
        # timer. The last thing that spoke unbidden here ran on a timer
        # and had to be deleted.
        self._repeats = repeats
        self._closed_ts = 0.0
        self._forgot_ts = 0.0
        self._tick_s = tick_s
        self._watch_every_s = watch_every_s
        self._watched_ts = 0.0
        self.engine_state = EngineState()
        self._awaiting: I.Intent | None = None
        self._seen_jobs: set[int] = set()
        self._noticed_once = False
        self.last_verdict: Verdict | None = None

    # -- the tick ------------------------------------------------------

    async def run(self) -> None:
        """Fourth task beside listen / stt / converse."""
        logger.info("engine: awake (tick %.0fs)", self._tick_s)
        while True:
            try:
                await asyncio.sleep(self._tick_s)
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                # A bookkeeping fault must never take the conversation
                # with it. The engine is an addition; its worst failure
                # should be a quiet assistant, not a dead one.
                logger.exception("engine: tick failed (non-fatal)")

    async def tick(self, now: float | None = None) -> Verdict:
        await self._notice()
        await self._look_around(now)
        await self._close_conversation(now)
        await self._forget_overheard(now)
        situation = await self.observe(now=now)
        pending = await self._store.pending()
        verdict = judge(situation, pending, self.engine_state)
        self.last_verdict = verdict
        if verdict.speak and verdict.intent is not None:
            await self._voice(verdict.intent, situation)
        return verdict

    async def observe(self, now: float | None = None) -> Situation:
        return await observe(
            state=self._state,
            persist=self._persist,
            jobs=self._jobs,
            unprompted_last_ts=self.engine_state.unprompted_last_ts,
            unprompted_times=self.engine_state.unprompted_times,
            idle=self._idle,
            now=now,
        )

    async def _look_around(self, now: float | None = None) -> None:
        """Sample the surroundings and keep whatever they made worth saying.

        Slower than the tick: the judge has to run every few seconds so a
        ripe intent lands promptly, but the room does not change that
        fast, and there is no reason to spend even a cheap sensor twelve
        times a minute.

        Everything here is absorbed. A watcher whose sensor fails should
        go blind, not take the conversation down with it.
        """
        if self._watch is None:
            return
        now = now if now is not None else time.time()
        if now - self._watched_ts < self._watch_every_s:
            return
        self._watched_ts = now
        try:
            from src.spine.environment import observe as observe_environment

            for change in self._watch.feed(await observe_environment(now=now)):
                await self.notice(
                    change.kind,
                    change.text,
                    origin=I.SELF,
                    urgency=change.urgency,
                    dedupe_key=change.dedupe_key,
                    # What you are doing is said in the present tense or
                    # not at all. Waiting is right for "the disk check
                    # finished"; here it would eventually say something
                    # that stopped being true while it waited.
                    expires_ts=now + change.ttl_s,
                )
        except Exception:  # noqa: BLE001
            logger.exception("engine: looking around failed (non-fatal)")

    async def _close_conversation(self, now: float | None = None) -> None:
        """Notice that a conversation is over, and write down what it was.

        Nothing had ended a conversation since 13 August — the code that
        did went with the engine deleted that day — so one row had been
        open for nine days, holding every turn since, and `summary` was
        a column with nowhere to be written. Both halves live here
        because both are about what outlives a turn, which is what this
        object is for.

        The model call happens only when something actually closed, so
        the usual cost of this method is one indexed query a minute.
        Everything is absorbed: a summary that cannot be written is a
        summary missing, not a conversation that could not end.
        """
        if self._persist is None:
            return
        now = now if now is not None else time.time()
        if now - self._closed_ts < CLOSE_EVERY_S:
            return
        self._closed_ts = now
        try:
            closed = await asyncio.to_thread(
                self._persist.close_idle_conversation,
                now=now,
                after_s=CONVERSATION_IDLE_S,
            )
            if closed is None:
                return
            conversation_id, said = closed
            if self._summarise is not None:
                summary = await self._summarise(said)
                if summary:
                    await asyncio.to_thread(
                        self._persist.save_summary, conversation_id, summary
                    )
                    logger.info(
                        "engine: conversation %d — %.90s", conversation_id, summary
                    )
        except Exception:  # noqa: BLE001
            logger.exception("engine: closing the conversation failed (non-fatal)")
            return

        await self._notice_repeats(now)

    async def _notice_repeats(self, now: float) -> None:
        """The only trigger this feature gets: a conversation ending.

        Not a timer. The last thing in this project that spoke unbidden
        ran on one — every N minutes, decide whether to say something —
        and `heartbeats`, the table it wrote to, is empty from birth
        because it was switched off before it ever filled. A conversation
        ending is the one moment there is new material *and* nobody is
        in the middle of talking.

        It runs after the summary rather than before it, so the
        conversation that just ended is part of what gets read. And it
        runs outside the try above: a fault here must not be able to
        stop a conversation being closed.
        """
        if self._repeats is None:
            return
        try:
            found = await self._repeats.look(now=now)
            if found is None:
                return
            dedupe_key, text = found
            # From here it is an ordinary intent, and inherits the whole
            # gate: not into silence, not at night, not mid-sentence, not
            # twice, quieter after being brushed off, and the model still
            # gets to refuse it.
            await self.notice(
                "repeat",
                text,
                origin=I.SELF,
                urgency=REPEAT_URGENCY,
                dedupe_key=dedupe_key,
            )
            logger.info("engine: it keeps coming up — %.80s", text)
        except Exception:  # noqa: BLE001
            logger.exception("engine: looking for repeats failed (non-fatal)")

    async def _forget_overheard(self, now: float | None = None) -> None:
        """Let go of what was said near it a week ago.

        Retention is the whole difference between keeping the room in
        working memory and making a recording nobody remembers starting.
        It runs here rather than at boot because this thing is meant to
        be running for weeks at a time, and a sweep that only happens at
        startup happens roughly never.

        Only overheard lines. Deleting an addressed one would be deleting
        a conversation the person had.
        """
        if self._persist is None:
            return
        now = now if now is not None else time.time()
        if now - self._forgot_ts < FORGET_EVERY_S:
            return
        self._forgot_ts = now
        try:
            gone = await asyncio.to_thread(
                self._persist.forget_overheard, before_ts=now - OVERHEARD_KEEP_S
            )
            if gone:
                logger.info("engine: forgot %d overheard lines", gone)
        except Exception:  # noqa: BLE001
            logger.exception("engine: forgetting failed (non-fatal)")

    # -- told from outside ---------------------------------------------

    async def notice(
        self,
        kind: str,
        text: str,
        *,
        origin: str = I.SELF,
        urgency: float = 0.5,
        dedupe_key: str | None = None,
        expires_ts: float | None = None,
    ) -> None:
        """Somewhere else in the system noticed something worth saying.

        This is what replaced the notification subsystem. That one had
        backends, quiet hours, per-kind cooldowns and a mode gate — 835
        lines deciding when a banner may appear — and none of it ever ran,
        because the single call that built it was deleted along with the
        engine it belonged to.

        All of those questions already have an answer here, and a better
        one: `judge` knows whether you are mid-sentence, whether it is
        night, whether it has been forward too often lately, and whether
        being brushed off last time should buy you quiet now. An event
        that becomes an intent inherits all of it. One that becomes a
        banner inherits none.

        And an intent that is never spoken is still not lost: it hangs in
        the prompt, so when you do start talking, it already knows what is
        outstanding between you.

        Never raises. A caller reporting trouble must not be given more.
        """
        try:
            await self._store.add(
                kind,
                text,
                origin=origin,
                urgency=urgency,
                dedupe_key=dedupe_key,
                expires_ts=expires_ts,
            )
        except Exception:  # noqa: BLE001
            logger.exception("engine: could not hold %r (non-fatal)", kind)

    # -- notice --------------------------------------------------------

    async def _notice(self) -> None:
        """Turn what happened into things worth raising.

        Only two sources, both read from what is already written down. No
        new sensors: an engine that grows eyes before it can hold its
        tongue is a worse problem than a quiet one.
        """
        if self._jobs is None:
            return
        try:
            first_pass, self._noticed_once = not self._noticed_once, True
            for job in await self._jobs.recent(limit=5):
                if job.id in self._seen_jobs or job.state not in ("done", "failed"):
                    continue
                self._seen_jobs.add(job.id)
                # Everything finished before this engine woke up already
                # happened without it. On the first live boot this read
                # five jobs from the previous week and formed five
                # intents to announce them — the text said "7 дн тому"
                # and it meant to say it anyway. Only what finished
                # around the restart is still news.
                if first_pass and job.age_seconds > STALE_JOB_S:
                    continue
                await self._store.add(
                    "job_done",
                    job.describe(),
                    origin=I.USER,
                    urgency=0.8,
                    dedupe_key=f"job:{job.id}",
                )
        except Exception:  # noqa: BLE001
            logger.exception("engine: noticing failed (non-fatal)")

    # -- speak ---------------------------------------------------------

    async def _voice(self, intent: I.Intent, situation: Situation) -> None:
        """Raise it — with one last chance for the model to say "not now".

        The conditions decide whether it *may* speak. This asks whether it
        *should*, which is the one judgement no rule can make, and the
        model is allowed to refuse.
        """
        text = intent.text
        if self._ask is not None:
            try:
                verdict = await self._ask(intent, situation)
                if not verdict:
                    await self._store.drop(intent.id, "не варте")
                    logger.info("engine: judged not worth saying — %.60s", intent.text)
                    return
                text = verdict
            except Exception:  # noqa: BLE001
                logger.exception("engine: ask failed — raising as written")

        await self._say(text)
        await self._store.mark_voiced(intent.id)
        # Tell the source it was actually said. Most of what reaches this
        # point is still refused above, and a source that cannot tell
        # "never raised" from "raised and ignored" has no way to know,
        # after a week, whether it is earning its place.
        await self._told_repeats(intent, said=True)
        self._awaiting = intent
        now = time.time()
        self.engine_state.unprompted_last_ts = now
        self.engine_state.unprompted_times.append(now)
        self.engine_state.unprompted_times = [
            t for t in self.engine_state.unprompted_times if now - t < 3600
        ]
        logger.info("engine: said unbidden — %.80s", text)

    async def _told_repeats(
        self, intent: I.Intent, *, said: bool = False, dismissed: bool = False
    ) -> None:
        """Report an intent's fate back to the source that raised it.

        Every intent is offered, not only the ones that came from here —
        the engine does not know which source made which key, and does
        not need to: a key that is not `repeats`' own is ignored by it.
        """
        if self._repeats is None:
            return
        try:
            if said:
                await self._repeats.mark_said(intent.dedupe_key)
            if dismissed:
                await self._repeats.dismiss(intent.dedupe_key)
        except Exception:  # noqa: BLE001
            logger.exception("engine: could not record how it landed (non-fatal)")

    # -- consequence ---------------------------------------------------

    async def observe_reply(self, user_text: str) -> None:
        """Called with whatever the user said next.

        This is the whole safeguard. Without it "speak freely" is a
        licence; with it, being brushed off costs the engine its
        patience, and the licence pays for itself.
        """
        awaiting, self._awaiting = self._awaiting, None
        outcome = reaction_to(user_text, awaiting)
        if outcome is None or awaiting is None:
            return

        await self._store.settle(awaiting.id, outcome)
        if outcome == I.REJECTED:
            # Trust already makes it quieter about everything. This is the
            # narrower half: told to leave this particular subject, it is
            # left for good, not until the next pass finds the same words
            # in next week's summaries.
            await self._told_repeats(awaiting, dismissed=True)
        before = self.engine_state.trust
        if outcome == I.ACCEPTED:
            self.engine_state.trust = max(TRUST_MIN, before * TRUST_DOWN)
        else:
            self.engine_state.trust = min(TRUST_MAX, before * TRUST_UP)
        logger.info(
            "engine: %s — довіра %.1f → %.1f (пауза %d хв)",
            outcome,
            before,
            self.engine_state.trust,
            int(BASE_QUIET_S * self.engine_state.trust / 60),
        )

    # -- for the prompt ------------------------------------------------

    async def prompt_block(self, now: float | None = None) -> str:
        """The present, and what is outstanding, for the system prompt.

        The half of this that never gets spoken is the half that matters
        most: when the user opens the conversation, the assistant answers
        knowing what is hanging between them instead of from a blank
        page.
        """
        try:
            situation = await self.observe(now=now)
            pending = await self._store.pending(limit=3)
        except Exception:  # noqa: BLE001
            logger.exception("engine: prompt block failed (non-fatal)")
            return ""

        lines = [f"Зараз: {situation.describe()}."]
        if pending:
            lines.append("Висить між вами:")
            lines.extend(f"- {i.describe()}" for i in pending)
        return "\n".join(lines)
