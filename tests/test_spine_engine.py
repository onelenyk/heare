"""When the assistant may speak first, and what it costs it to be wrong.

``judge`` is a pure function of the present: no clock, no database, no
network. That is the whole reason these can be a table of cases instead
of an afternoon spent listening to see whether it interrupts.

The one thing not testable here is the only thing that finally matters —
whether being spoken to unbidden is pleasant or irritating. That needs a
room and a person. Everything below is what has to hold before it is
worth anyone's evening.
"""

from __future__ import annotations

import asyncio

import pytest

from src.spine import intents as I
from src.spine.engine import (
    BASE_QUIET_S,
    TRUST_MAX,
    TRUST_MIN,
    Engine,
    EngineState,
    judge,
    reaction_to,
)
from src.spine.situation import Situation

NOW = 1_000_000.0


def sit(**kw) -> Situation:
    base = dict(
        now=NOW,
        hour=14,
        minute=0,
        weekday=1,
        silence_s=600.0,
        user_silence_s=60.0,
        bot_state="idle",
        bot_state_s=60.0,
        jobs_running=0,
        unprompted_last_s=1e9,
        unprompted_1h=0,
    )
    base.update(kw)
    return Situation(**base)


def intent(**kw) -> I.Intent:
    base = dict(
        id=1,
        kind="job_done",
        text="перевірка диска скінчилась",
        origin=I.USER,
        urgency=0.8,
        state=I.PENDING,
        created_ts=NOW - 300,
        updated_ts=NOW - 300,
    )
    base.update(kw)
    return I.Intent(**base)


# ── when it holds its tongue ──────────────────────────────────────────


def test_nothing_pending_is_not_an_occasion() -> None:
    assert judge(sit(), [], EngineState()).speak is False


@pytest.mark.parametrize("state", ["talking", "listening", "thinking", "speaking"])
def test_never_mid_turn(state: str) -> None:
    """Said in the middle of an exchange it is an interruption, whatever
    it is about."""
    verdict = judge(sit(bot_state=state), [intent()], EngineState())
    assert verdict.speak is False
    assert "розмова" in verdict.reason


def test_not_to_an_empty_room() -> None:
    """Speaking to nobody spends the intent and buys nothing."""
    assert judge(sit(user_silence_s=4000), [intent()], EngineState()).speak is False


def test_lets_the_person_finish() -> None:
    """The microphone says they stopped; a remark landing on the tail of
    their sentence still reads as cutting in."""
    assert judge(sit(user_silence_s=3), [intent()], EngineState()).speak is False


def test_an_intent_that_is_not_due_waits() -> None:
    later = intent(due_ts=NOW + 3600)
    assert judge(sit(), [later], EngineState()).speak is False


# ── night ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("hour", [23, 2, 6])
def test_at_night_its_own_noticings_wait_for_morning(hour: int) -> None:
    mine = intent(origin=I.SELF, urgency=0.9)
    verdict = judge(sit(hour=hour), [mine], EngineState())
    assert verdict.speak is False
    assert verdict.reason == "ніч"


def test_at_night_an_urgent_errand_still_lands() -> None:
    """What you asked for is different from what it thought of."""
    assert judge(sit(hour=2), [intent(urgency=0.9)], EngineState()).speak is True


def test_at_night_a_dull_errand_does_not() -> None:
    assert judge(sit(hour=2), [intent(urgency=0.4)], EngineState()).speak is False


# ── pace ──────────────────────────────────────────────────────────────


def test_it_does_not_speak_twice_in_a_row() -> None:
    just_spoke = sit(unprompted_last_s=60)
    assert judge(just_spoke, [intent()], EngineState()).speak is False


def test_urgency_buys_impatience_but_not_the_whole_wait() -> None:
    half = BASE_QUIET_S * 0.55
    urgent = judge(sit(unprompted_last_s=half), [intent(urgency=1.0)], EngineState())
    dull = judge(sit(unprompted_last_s=half), [intent(urgency=0.1)], EngineState())
    assert urgent.speak is True
    assert dull.speak is False


def test_being_brushed_off_makes_it_wait_longer() -> None:
    """The safeguard, in one assertion: the same situation, the same
    intent, and the only difference is how the last remark landed."""
    situation = sit(unprompted_last_s=BASE_QUIET_S * 1.2)
    trusting = EngineState(trust=TRUST_MIN)
    burnt = EngineState(trust=4.0)

    assert judge(situation, [intent()], trusting).speak is True
    assert judge(situation, [intent()], burnt).speak is False


def test_the_most_urgent_one_is_the_one_raised() -> None:
    dull = intent(id=1, urgency=0.2, text="дрібниця")
    sharp = intent(id=2, urgency=0.9, text="важливе")
    verdict = judge(sit(), [dull, sharp], EngineState())
    assert verdict.intent is not None and verdict.intent.id == 2


def test_every_silence_can_say_why() -> None:
    """A proactive system whose quiet cannot be interrogated is one you
    can only debug by waiting."""
    for situation, pending in (
        (sit(), []),
        (sit(bot_state="speaking"), [intent()]),
        (sit(hour=3), [intent(origin=I.SELF)]),
        (sit(unprompted_last_s=10), [intent()]),
    ):
        assert judge(situation, pending, EngineState()).reason


# ── reading how it landed ─────────────────────────────────────────────


def test_being_waved_away_is_unmistakable() -> None:
    assert reaction_to("не зараз, потім", intent()) == I.REJECTED
    assert reaction_to("помовч трохи", intent()) == I.REJECTED


def test_answering_on_the_subject_counts_as_accepted() -> None:
    assert reaction_to("а що там з перевіркою диска?", intent()) == I.ACCEPTED


def test_talking_about_something_else_counts_as_ignored() -> None:
    assert reaction_to("яка завтра погода", intent()) == I.IGNORED


def test_nothing_to_read_when_it_said_nothing() -> None:
    assert reaction_to("будь-що", None) is None


# ── consequence ───────────────────────────────────────────────────────


class _Store:
    def __init__(self) -> None:
        self.settled: list[tuple[int, str]] = []
        self.voiced: list[int] = []
        self.dropped: list[int] = []
        self.items: list[I.Intent] = []

    async def pending(self, limit: int = 10):
        return self.items[:limit]

    async def mark_voiced(self, intent_id):
        self.voiced.append(intent_id)

    async def settle(self, intent_id, outcome):
        self.settled.append((intent_id, outcome))

    async def drop(self, intent_id, reason=""):
        self.dropped.append(intent_id)

    async def add(self, *a, **kw):
        return None


class _Persist:
    """Someone spoke a minute ago. Without this the engine correctly
    decides there is nobody to speak to — missing data must not read as
    presence."""

    def last_turn_times(self):
        return {"any": NOW - 60, "user": NOW - 60}


def _engine(**kw) -> tuple[Engine, _Store, list[str]]:
    store = _Store()
    said: list[str] = []

    async def say(text: str) -> None:
        said.append(text)

    kw.setdefault("persist", _Persist())
    return Engine(store=store, say=say, **kw), store, said


def test_being_ignored_costs_it_patience() -> None:
    engine, store, _ = _engine()
    engine._awaiting = intent()

    asyncio.run(engine.observe_reply("яка завтра погода"))

    assert store.settled == [(1, I.IGNORED)]
    assert engine.engine_state.trust > TRUST_MIN


def test_being_answered_earns_it_back() -> None:
    engine, store, _ = _engine()
    engine.engine_state.trust = 4.0
    engine._awaiting = intent()

    asyncio.run(engine.observe_reply("і що показала перевірка диска?"))

    assert store.settled == [(1, I.ACCEPTED)]
    assert engine.engine_state.trust < 4.0


def test_patience_has_a_ceiling() -> None:
    """Otherwise a bad afternoon silences it for a week."""
    engine, _, _ = _engine()
    for _ in range(20):
        engine._awaiting = intent()
        asyncio.run(engine.observe_reply("не зараз"))
    assert engine.engine_state.trust <= TRUST_MAX


def test_a_reply_to_nothing_changes_nothing() -> None:
    engine, store, _ = _engine()
    asyncio.run(engine.observe_reply("просто розмова"))
    assert store.settled == []
    assert engine.engine_state.trust == TRUST_MIN


# ── speaking ──────────────────────────────────────────────────────────


def test_it_speaks_through_the_conductor() -> None:
    """No second mouth: the remark goes in as a turn so the model phrases
    it in voice, in the right language."""
    engine, store, said = _engine()
    store.items = [intent()]

    verdict = asyncio.run(engine.tick(now=NOW))

    assert verdict.speak is True
    assert said and "диска" in said[0]
    assert store.voiced == [1]


def test_the_model_may_still_refuse() -> None:
    """Conditions decide whether it may; this decides whether it should —
    the one judgement no rule can make."""

    async def ask(_intent, _situation):
        return None

    engine, store, said = _engine(ask=ask)
    store.items = [intent()]

    verdict = asyncio.run(engine.tick(now=NOW))

    assert verdict.speak is True  # the conditions let it through
    assert said == []  # and the judgement stopped it
    assert store.dropped == [1]


def test_a_refusal_from_the_model_is_not_fatal() -> None:
    """It is an addition; its worst failure should be a quiet assistant."""

    async def ask(_intent, _situation):
        raise RuntimeError("provider down")

    engine, store, said = _engine(ask=ask)
    store.items = [intent()]

    asyncio.run(engine.tick(now=NOW))
    assert said, "a failing judge must fall back to raising it as written"


def test_the_prompt_carries_what_is_outstanding() -> None:
    """The half that is never spoken is the half that matters most: when
    the user opens the conversation, it answers knowing what hangs
    between them."""
    engine, store, _ = _engine()
    store.items = [intent()]

    block = asyncio.run(engine.prompt_block(now=NOW))

    assert "Зараз:" in block
    assert "Висить між вами:" in block
    assert "диска" in block
