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

# How long it waits between unbidden remarks at full trust. Multiplied by
# trust, which climbs when it is brushed off.
BASE_QUIET_S = 15 * 60.0

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
        tick_s: float = TICK_S,
    ) -> None:
        self._store = store
        self._say = say  # async (text) -> None — normally loop.inject
        self._state = state
        self._persist = persist
        self._jobs = jobs
        self._ask = ask  # async (intent, situation) -> str | None
        self._tick_s = tick_s
        self.engine_state = EngineState()
        self._awaiting: I.Intent | None = None
        self._seen_jobs: set[int] = set()
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
            now=now,
        )

    # -- told from outside ---------------------------------------------

    async def notice(
        self,
        kind: str,
        text: str,
        *,
        origin: str = I.SELF,
        urgency: float = 0.5,
        dedupe_key: str | None = None,
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
            for job in await self._jobs.recent(limit=5):
                if job.id in self._seen_jobs or job.state not in ("done", "failed"):
                    continue
                self._seen_jobs.add(job.id)
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
        self._awaiting = intent
        now = time.time()
        self.engine_state.unprompted_last_ts = now
        self.engine_state.unprompted_times.append(now)
        self.engine_state.unprompted_times = [
            t for t in self.engine_state.unprompted_times if now - t < 3600
        ]
        logger.info("engine: said unbidden — %.80s", text)

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
