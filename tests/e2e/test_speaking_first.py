"""Speaking unbidden: when it may, when it must not, and what it costs.

This is what the engine was built for and the riskiest thing in the
project — an assistant that is slow gets tolerated, an assistant that
intrudes gets switched off. Its own docstring says the safeguard is not
a table of limits but consequence: answered on the subject and it grows
bolder, waved away and it goes quiet for longer.

That consequence had never once seen a reaction. Found here, on the day
this file was written.
"""

from __future__ import annotations

import time

import pytest

from tests.e2e.room import close_room, open_room

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]

DAY = 86400.0


@pytest.fixture
async def room(tmp_path):
    r = await open_room(tmp_path)
    try:
        yield r
    finally:
        await close_room(r)


def at(hour: int) -> float:
    """A timestamp at a given local hour, for the rules about night."""
    now = time.localtime()
    return time.mktime(
        (now.tm_year, now.tm_mon, now.tm_mday, hour, 30, 0, 0, 0, -1)
    )


async def cause(room, text: str = "диск перевірено, все гаразд", **kw) -> None:
    await room.loop.engine.notice("job_done", text, urgency=0.8, **kw)


# ── when it may ───────────────────────────────────────────────────────


async def test_it_says_the_thing_when_there_is_cause(room) -> None:
    await cause(room)
    room.will_say("Диск перевірено.")

    verdict = await room.tick(now=at(14))

    assert verdict.speak is True
    assert verdict.reason == "є привід"


async def test_nothing_pending_is_not_an_occasion(room) -> None:
    verdict = await room.tick(now=at(14))
    assert verdict.speak is False


# ── when it must not ──────────────────────────────────────────────────


async def test_it_does_not_speak_into_the_middle_of_a_sentence(room) -> None:
    """The guard that could not fire for as long as the spine existed:
    the reader looked for words no writer produced, so `busy_talking` was
    false through the middle of every reply."""
    await cause(room)
    room.is_talking(True)

    verdict = await room.tick(now=at(14))

    assert verdict.speak is False
    assert verdict.reason == "розмова триває"


async def test_it_does_not_speak_to_an_empty_room(room) -> None:
    """Speaking to nobody spends the intent and buys nothing."""
    await cause(room)
    room.away_for(2 * 3600)

    verdict = await room.tick(now=at(14))

    assert verdict.speak is False
    assert verdict.reason == "нікого немає"


async def test_working_in_silence_is_not_an_empty_room(room) -> None:
    """The hour someone works without saying a word is the hour this
    exists for — and was the hour it concluded nobody was there."""
    await cause(room)
    room.away_for(5)

    assert (await room.tick(now=at(14))).speak is True


async def test_at_night_only_what_you_asked_for(room) -> None:
    """Being loud at 02:00 is what gets a device switched off for good.
    Its own noticings can wait until morning."""
    from src.spine import intents as I

    await room.loop.engine.notice(
        "watched", "ти дві години в одному вікні", origin=I.SELF, urgency=0.4
    )

    verdict = await room.tick(now=at(2))

    assert verdict.speak is False
    assert verdict.reason == "ніч"


# ── what it costs ─────────────────────────────────────────────────────


async def test_its_own_remark_is_not_the_persons_answer(room) -> None:
    """The engine speaks through the injection queue, and an injected
    line is processed as a turn — so the first thing to arrive after an
    unbidden remark was the remark itself. Its own words matched its own
    intent perfectly, scored as agreement, settled the intent and cleared
    what it was waiting on. Trust never moved once, in either direction,
    for the whole life of the feature.
    """
    await cause(room)
    room.will_say("Диск перевірено.")
    await room.tick(now=at(14))
    await room.drained()  # let its own remark come back through the queue

    assert room.loop.engine._awaiting is not None, (
        "it heard itself and called that an answer"
    )


async def test_being_waved_away_costs_it_patience(room) -> None:
    await cause(room)
    room.will_say("Диск перевірено.")
    await room.tick(now=at(14))
    await room.drained()
    before = room.loop.engine.engine_state.trust

    room.will_say("Гаразд.")
    await room.told("не зараз, помовч")

    assert room.loop.engine.engine_state.trust > before


async def test_answering_on_the_subject_wins_some_back(room) -> None:
    """Otherwise the only direction trust can move is towards silence,
    and an assistant that can only ever get quieter is one that will
    eventually stop."""
    engine = room.loop.engine
    engine.engine_state.trust = 4.0

    await cause(room, "бекап на другий диск завершився")
    room.will_say("Бекап на другий диск завершився.")
    await room.tick(now=at(14))
    await room.drained()

    room.will_say("Добре.")
    await room.told("а бекап на другий диск точно завершився?")

    assert engine.engine_state.trust < 4.0


# ── the model's veto ──────────────────────────────────────────────────


async def test_the_model_may_refuse_and_then_it_stays_quiet(room) -> None:
    """The conditions decide whether it *may* speak. This is the one
    judgement no rule can make — and live, it stopped five reports of
    week-old work in a row."""
    await cause(room, "7 дн тому: стара робота — готово")
    room.will_say("НІ")

    await room.tick(now=at(14))

    assert room.rows() == [], "nothing reached the conversation"
    assert room.intents("dropped"), "and the intent was let go, not left waiting"


async def test_a_start_does_not_announce_last_week(room) -> None:
    """On its first live boot it read five finished jobs — four of them a
    week old — and formed an intent for each. The text of every one began
    "7 дн тому", so it knew, and meant to say it anyway."""
    engine = room.loop.engine

    class _Job:
        def __init__(self, job_id, age_s, text):
            self.id, self.age_seconds, self.state = job_id, age_s, "done"
            self._text = text

        def describe(self):
            return self._text

    class _Jobs:
        def __init__(self, jobs):
            self.jobs = jobs

        async def recent(self, limit=5):
            return self.jobs[:limit]

    engine._jobs = _Jobs([
        _Job(1, 7 * DAY, "7 дн тому: стара робота"),
        _Job(2, 30.0, "щойно: свіжа робота"),
    ])

    await engine._notice()

    kept = [text for _kind, text, _u, _s in room.intents()]
    assert "щойно: свіжа робота" in kept
    assert "7 дн тому: стара робота" not in kept
