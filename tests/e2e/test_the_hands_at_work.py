"""The hands at work: what a person notices while a job runs.

`delegate` returns at once and the job runs in the background, so the
questions worth asking are about the time in between. Is the person still
answered while the hands are busy? Can two jobs' answers be told apart?
Does «стоп» stop the work, not only the voice? Does a job the last process
dropped get mentioned, or does it just vanish? And when the model says
«гляну» and then calls nothing, does anything happen at all?

Only the worker's model call is replaced (`hire`); the toolbox, the real
`Hands`, the jobs table and the loop's two lanes are all real.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

from tests.e2e.room import Says, close_room, hire, lines, open_room, until

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.e2e_text]


@pytest.fixture
async def room(tmp_path):
    r = await open_room(tmp_path)
    try:
        yield r
    finally:
        await close_room(r)


@pytest.fixture
def worker(monkeypatch):
    return lambda *answers: hire(monkeypatch, *answers)


def _last_user_line(messages: list[dict]) -> str:
    return next(m["content"] for m in reversed(messages) if m["role"] == "user")


# ── the person is not kept waiting ────────────────────────────────────


async def test_a_busy_worker_does_not_keep_the_person_waiting(room, worker) -> None:
    """The split exists for this: while a long job runs, a new question is
    answered now, not after the job. Then the job's own answer still
    arrives."""
    hands = worker("Найбільше навантажує Chrome.")
    hands.gate = asyncio.Event()
    room.will_say(
        Says(calls=(("delegate", {"task": "що найбільше навантажує комп'ютер"}),)),
        "Сьогодні п'ятниця.",
        "Найбільше навантажує Chrome.",
    )

    assert await room.told("Дока, глянь, що жере ресурси") == "Прийнято, роблю."
    await until(lambda: hands.calls)

    assert await room.told("Дока, а який сьогодні день?") == "Сьогодні п'ятниця."

    hands.gate.set()
    await until(lambda: "Найбільше навантажує Chrome." in lines(room))


async def test_two_answers_at_once_can_be_told_apart(room, worker) -> None:
    """Two jobs finishing close together would be two unlabelled answers
    arriving in whatever order. With more than one in flight each result
    carries a few words of its task."""
    hands = worker("Chrome, потім Telegram.", "Вільно сорок гігабайтів.")
    hands.gate = asyncio.Event()
    room.will_say(
        Says(calls=(("delegate", {"task": "перевір процеси системи"}),)),
        Says(calls=(("delegate", {"task": "перевір вільне місце диску"}),)),
        "Про процеси: Chrome, потім Telegram.",
        "Про диск: вільно сорок гігабайтів.",
    )

    await room.told("Дока, глянь процеси")
    await room.told("Дока, і скільки місця на диску")
    await until(lambda: len(hands.calls) == 2)
    hands.gate.set()
    await until(lambda: "Про диск: вільно сорок гігабайтів." in lines(room))

    delivered = [
        _last_user_line(m) for m in room.prompts if "[результат роботи]" in _last_user_line(m)
    ]
    assert len(delivered) == 2
    # The first to land still had its twin in flight, so it names itself.
    assert "[перевір процеси системи]" in delivered[0] or (
        "[перевір вільне місце]" in delivered[0]
    ), delivered


# ── «стоп» means the work too ─────────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason="src/spine/loop.py ~256 — cancel_all runs only at shutdown (and from "
    "the dashboard button); «стоп» as a turn goes to the model, the job runs on "
    "and its answer is said anyway",
)
async def test_stop_calls_off_the_work_and_its_answer(room, worker) -> None:
    """Observed before (findings/two-agents.md): the voice fell silent on
    «стоп» and, seconds later, announced the result of the work the person
    had just called off. That reads as not listening."""
    hands = worker("Опубліковано чотири додатки.")
    hands.gate = asyncio.Event()
    room.will_say(
        Says(calls=(("delegate", {"task": "опублікуй чотири додатки"}),)),
        "Зупинилась.",
        "Опубліковано чотири додатки.",
    )

    await room.told("Дока, опублікуй чотири додатки")
    await until(lambda: hands.calls)
    await room.told("Дока, стоп")
    hands.gate.set()
    await asyncio.sleep(0.5)

    assert "Опубліковано чотири додатки." not in lines(room)
    assert not any("[результат роботи]" in _last_user_line(m) for m in room.prompts)


# ── nothing vanishes ────────────────────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason="src/spine/main.py ~405 — a job swept as interrupted at start is "
    "logged, never said",
)
async def test_a_job_the_last_run_dropped_is_mentioned(tmp_path) -> None:
    """A restart mid-job used to leave the person waiting for an answer
    that could never come. The job is found at start (swept to
    `interrupted`); the person should hear that it was cut off."""
    first = await open_room(tmp_path)
    await close_room(first)
    with sqlite3.connect(tmp_path / "heare.db") as db:
        db.execute(
            "INSERT INTO jobs (label, task, state, created_ts, updated_ts)"
            " VALUES (?, ?, 'running', ?, ?)",
            ("опублікуй чотири додатки", "опублікуй чотири додатки",
             time.time() - 60, time.time() - 60),
        )

    second = await open_room(tmp_path)
    try:
        with sqlite3.connect(tmp_path / "heare.db") as db:
            state = db.execute("SELECT state FROM jobs").fetchone()[0]
        assert state == "interrupted", "the sweep itself must work for this test to mean anything"
        await second.drained()
        assert any("опубл" in line for line in lines(second)), lines(second)
    finally:
        await close_room(second)


# ── saying is not doing ────────────────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason="the model says it will act and calls nothing; ~10% of live turns "
    "(tests/test_spine_verbs_live.py) — the turn just ends and no answer ever comes",
)
async def test_announcing_work_is_not_the_work(room, worker) -> None:
    """«Гляну, секунду.» with no tool call is a promise nobody keeps: the
    person waits for an answer that was never asked for. Either the work
    starts, or the person is told it did not."""
    hands = worker("Найбільше навантажує Chrome.")
    room.will_say(Says(text="Гляну, секунду."))

    await room.told("Дока, що жере ресурси?")
    await asyncio.sleep(0.5)

    assert hands.calls, "said it would look, and nothing looked"
