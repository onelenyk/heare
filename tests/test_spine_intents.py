"""What it means to say, kept somewhere that outlives the process.

The store mirrors ``src/agent/jobs.py`` on purpose — same table shape,
same "never raise into the caller" rule. The difference between the two
is the point: a job is finished when the work is done, an intent only
when it has landed with a person.
"""

from __future__ import annotations

import time

import aiosqlite
import pytest
import pytest_asyncio

from src.spine import intents as I

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def store(tmp_path):
    db = await aiosqlite.connect(str(tmp_path / "intents.db"))
    s = I.IntentStore(db)
    await s.init()
    yield s
    await db.close()


async def test_an_intent_survives_being_written(store) -> None:
    await store.add("job_done", "перевірка диска скінчилась", origin=I.USER)

    pending = await store.pending()

    assert [i.text for i in pending] == ["перевірка диска скінчилась"]
    assert pending[0].origin == I.USER
    assert pending[0].is_mine is False


async def test_its_own_noticings_are_marked_as_such(store) -> None:
    """`origin` is what makes "what does it want" answerable at all."""
    await store.add("unanswered", "чим скінчилась зустріч", origin=I.SELF)

    assert (await store.pending())[0].is_mine is True


async def test_noticing_the_same_thing_twice_adds_it_once(store) -> None:
    """The engine re-reads the same finished job on every tick. Without
    this, each pass would add another copy of the same remark and the
    assistant would eventually say it several times over."""
    for _ in range(4):
        await store.add("job_done", "готово", dedupe_key="job:7")

    assert len(await store.pending()) == 1


async def test_the_most_urgent_comes_first(store) -> None:
    await store.add("a", "дрібниця", urgency=0.1)
    await store.add("b", "важливе", urgency=0.9)

    assert [i.text for i in await store.pending()] == ["важливе", "дрібниця"]


async def test_something_not_yet_due_is_still_pending_but_not_ripe(store) -> None:
    """Worth saying, but not now — a reminder is the obvious case."""
    later = time.time() + 3600
    await store.add("reminder", "нагадати про рахунок", due_ts=later)

    intent = (await store.pending())[0]

    assert intent.ripe() is False
    assert intent.ripe(later + 1) is True


async def test_raising_it_takes_it_out_of_the_queue(store) -> None:
    intent_id = await store.add("job_done", "готово")
    await store.mark_voiced(intent_id)

    assert await store.pending() == []
    awaiting = await store.awaiting_reaction()
    assert awaiting is not None and awaiting.id == intent_id


async def test_how_it_landed_is_recorded(store) -> None:
    intent_id = await store.add("job_done", "готово")
    await store.mark_voiced(intent_id)
    await store.settle(intent_id, I.ACCEPTED)

    assert await store.awaiting_reaction() is None
    assert (await store.recent())[0].outcome == I.ACCEPTED


async def test_a_subject_raised_once_is_not_raised_again(store) -> None:
    """Being ignored closes it too. One attempt is enough to have tried;
    coming back to it is how an assistant becomes a nag."""
    intent_id = await store.add("job_done", "готово")
    await store.mark_voiced(intent_id)
    await store.settle(intent_id, I.IGNORED)

    assert await store.pending() == []


async def test_a_remark_nobody_answered_is_let_go(store) -> None:
    intent_id = await store.add("job_done", "готово")
    await store.mark_voiced(intent_id)

    swept = await store.sweep_stale(now=time.time() + I.STALE_AFTER_S + 60)

    assert swept == 1
    assert await store.awaiting_reaction() is None
    assert (await store.recent())[0].outcome == I.IGNORED


async def test_bookkeeping_failure_never_reaches_the_conversation(store) -> None:
    """Every method swallows its own errors for the same reason the job
    store does: a dropped row costs one subject, an exception on the
    conversational path costs the conversation."""
    await store._db.close()

    assert await store.add("job_done", "готово") is None
    assert await store.pending() == []
    assert await store.recent() == []
    await store.mark_voiced(1)
    await store.settle(1, I.ACCEPTED)


async def test_it_reads_aloud_as_a_sentence(store) -> None:
    await store.add("job_done", "перевірка диска скінчилась")

    assert (await store.pending())[0].describe().startswith("щойно:")
