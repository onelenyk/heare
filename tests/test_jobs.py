"""Delegated work that outlives the turn — and the process.

A job used to exist only as an asyncio task: it began, it ended, and
nothing remained. The assistant could not say what it did an hour ago,
and a restart mid-job left the user waiting for an answer that could
never arrive.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from src.agent.jobs import (
    CANCELLED,
    DONE,
    FAILED,
    INTERRUPTED,
    RUNNING,
    JobStore,
    _ago,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def store():
    with tempfile.TemporaryDirectory() as tmp:
        db = await aiosqlite.connect(Path(tmp) / "jobs.db")
        s = JobStore(db)
        await s.init()
        yield s
        await db.close()


async def test_a_job_records_the_goal_in_the_users_words(store: JobStore) -> None:
    job_id = await store.start("подивись диск", "подивись скільки місця на диску")
    assert job_id is not None

    [job] = await store.recent()
    assert job.task == "подивись скільки місця на диску"
    assert job.state == RUNNING


async def test_steps_are_kept_so_an_interrupted_job_can_say_how_far_it_got(
    store: JobStore,
) -> None:
    job_id = await store.start("диск", "подивись диск")
    await store.step(job_id, "bash(df -h)")
    await store.step(job_id, "read(/etc/fstab)")

    [job] = await store.recent()
    assert job.steps == ["bash(df -h)", "read(/etc/fstab)"]


async def test_the_step_log_does_not_grow_without_bound(store: JobStore) -> None:
    job_id = await store.start("довге", "щось довге")
    for i in range(40):
        await store.step(job_id, f"крок {i}")

    [job] = await store.recent()
    assert len(job.steps) == 20
    assert job.steps[-1] == "крок 39"


@pytest.mark.parametrize(
    "state, kwargs, expected",
    [
        (DONE, {"result": "240 ГБ вільно"}, "240 ГБ вільно"),
        (FAILED, {"error": "no such file"}, None),
        (CANCELLED, {}, None),
    ],
)
async def test_a_job_ends_in_a_recorded_state(
    store: JobStore, state: str, kwargs: dict, expected: str | None
) -> None:
    job_id = await store.start("щось", "щось зроби")
    await store.finish(job_id, state, **kwargs)

    [job] = await store.recent()
    assert job.state == state
    assert job.result == expected


async def test_a_restart_marks_running_jobs_interrupted(store: JobStore) -> None:
    """Nothing but the process ending can leave a job running, so anything
    found in that state at startup was cut off."""
    await store.start("перше", "подивись диск")
    await store.start("друге", "знайди файл")
    finished = await store.start("третє", "вже готове")
    await store.finish(finished, DONE, result="ok")

    stranded = await store.sweep_interrupted()

    assert {j.task for j in stranded} == {"подивись диск", "знайди файл"}
    assert all(j.state == RUNNING for j in stranded), "reported as they were"
    assert {j.state for j in await store.recent()} == {INTERRUPTED, DONE}


async def test_sweeping_twice_finds_nothing_the_second_time(
    store: JobStore,
) -> None:
    await store.start("щось", "щось")
    assert len(await store.sweep_interrupted()) == 1
    assert await store.sweep_interrupted() == []


async def test_recent_answers_what_were_you_doing(store: JobStore) -> None:
    for i in range(7):
        job_id = await store.start(f"робота {i}", f"завдання {i}")
        await store.finish(job_id, DONE, result=f"результат {i}")

    recent = await store.recent(limit=3)
    assert len(recent) == 3
    assert recent[0].task == "завдання 6", "newest first"


async def test_a_broken_database_never_raises_into_the_caller() -> None:
    """A failure to record must not take the job down with it."""

    class Broken:
        async def execute(self, *a, **kw):
            raise RuntimeError("disk on fire")

        async def executescript(self, *a, **kw):
            raise RuntimeError("disk on fire")

        async def commit(self):
            raise RuntimeError("disk on fire")

    store = JobStore(Broken())
    assert await store.start("x", "y") is None
    await store.step(1, "anything")
    await store.finish(1, DONE, result="ok")
    assert await store.recent() == []
    assert await store.sweep_interrupted() == []


# ── reading it back out loud ──────────────────────────────────────────


async def test_ago_is_spoken_not_printed() -> None:
    assert _ago(10) == "щойно"
    assert _ago(600) == "10 хв тому"
    assert _ago(7200) == "2 год тому"
    assert _ago(200000) == "2 дн тому"


async def test_describe_says_what_happened(store: JobStore) -> None:
    job_id = await store.start("диск", "подивись диск")
    await store.finish(job_id, DONE, result="240 ГБ вільно")
    [job] = await store.recent()
    assert "подивись диск" in job.describe()
    assert "240 ГБ вільно" in job.describe()

    job.state = INTERRUPTED
    assert "обірвалось" in job.describe()


async def test_age_is_measured_from_the_start(store: JobStore) -> None:
    job_id = await store.start("щось", "щось")
    await store.finish(job_id, DONE, result="ok")
    [job] = await store.recent()
    assert job.age_seconds < 5
    assert job.created_ts <= time.time()
