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
    await room.said_to_it("Дока, стоп")
    hands.gate.set()
    await asyncio.sleep(0.5)

    assert "Зупинила." in [text for text, _voice in room.voiced]
    assert "Опубліковано чотири додатки." not in lines(room)
    assert not any("[результат роботи]" in _last_user_line(m) for m in room.prompts)


async def test_stop_with_nothing_running_is_answered_without_the_model(room) -> None:
    """A command is caught in code, every time: the model is not asked
    whether «стоп» meant stop."""
    asked = len(room.prompts)

    await room.said_to_it("Дока, стоп")

    assert len(room.prompts) == asked
    assert "Добре." in [text for text, _voice in room.voiced]


# ── nothing vanishes ────────────────────────────────────────────────────


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
        second.will_say("Публікація чотирьох додатків перервалась — повторити?")
        with sqlite3.connect(tmp_path / "heare.db") as db:
            state = db.execute("SELECT state FROM jobs").fetchone()[0]
        assert state == "interrupted", "the sweep itself must work for this test to mean anything"
        await until(lambda: any("перервалась" in line for line in lines(second)))
        told = _last_user_line(second.prompts[0])
        assert "опублікуй чотири додатки" in told and "перезапуск" in told
    finally:
        await close_room(second)


async def test_a_job_dropped_long_ago_is_not_news(tmp_path) -> None:
    """A start once announced week-old jobs, which is worse than saying
    nothing: only what the last run dropped recently is mentioned."""
    first = await open_room(tmp_path)
    await close_room(first)
    with sqlite3.connect(tmp_path / "heare.db") as db:
        db.execute(
            "INSERT INTO jobs (label, task, state, created_ts, updated_ts)"
            " VALUES ('старе', 'старе доручення', 'running', ?, ?)",
            (time.time() - 3 * 86400, time.time() - 3 * 86400),
        )

    second = await open_room(tmp_path)
    try:
        await second.drained()
        assert second.prompts == []
    finally:
        await close_room(second)


# ── saying is not doing ────────────────────────────────────────────────


async def test_announcing_work_is_not_the_work(room, worker) -> None:
    """«Гляну, секунду.» with no tool call is a promise nobody keeps: the
    person waits for an answer that was never asked for. Either the work
    starts, or the person is told it did not."""
    hands = worker("Найбільше навантажує Chrome.")
    room.will_say(Says(text="Гляну, секунду."))

    await room.told("Дока, що жере ресурси?")
    await asyncio.sleep(0.5)

    assert hands.calls, "said it would look, and nothing looked"


async def test_a_promised_reminder_is_set_not_handed_off(room) -> None:
    """«Ставлю нагадування» with no call: asked once more, the model makes
    the call it meant. A reminder is the voice's own verb — the hands are
    shown a `remind` they cannot run, so sending it there would fail."""
    room.will_say(
        Says(text="Ставлю нагадування на десять хвилин."),
        Says(calls=(("remind", {"minutes": 10, "text": "випити води"}),)),
    )

    await room.told("Дока, нагадай за десять хвилин випити води")
    await until(lambda: "Добре, нагадаю за 10 хвилин." in [t for t, _v in room.voiced])

    assert room.intents(), "the reminder must actually be held"


async def test_the_hands_can_set_a_reminder(room, monkeypatch) -> None:
    """«Нагадай, коли закінчиться білд» is a job that ends in a line said
    later. The hands are shown `remind`; it runs as the voice's own verb,
    lent to them, so the reminder is held exactly as if the voice had set
    it — and the action is written down like any other."""
    hands = hire(
        monkeypatch,
        {"tool_calls": [("remind", {"minutes": 15, "text": "білд мав закінчитись"})]},
        "Поставила нагадування за 15 хвилин.",
    )
    room.will_say(
        Says(calls=(("delegate", {"task": "нагадай за 15 хвилин про білд"}),)),
        "Нагадаю про білд за чверть години.",
    )

    await room.told("Дока, нагадай за чверть години глянути білд")
    await until(lambda: "Нагадаю про білд за чверть години." in lines(room))

    assert "remind" in hands.offered(0)
    assert any("білд" in str(row) for row in room.intents()), room.intents()
    tool_result = next(m for m in hands.calls[1][0] if m["role"] == "tool")
    assert tool_result["content"] == "Добре, нагадаю за 15 хвилин."

