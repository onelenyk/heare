"""The engine's first job: work ending becomes an intent, and is told."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime

from heare.core.conversation import RESULT_PREFIX
from heare.engine import Engine, Intents


@dataclass
class Ev:
    kind: str
    data: dict


def event(status, **over):
    data = {"id": 7, "request": "зроби сайт", "news": "Сайт готовий.", "status": status}
    data.update(over)
    return Ev("task", data)


def at(day, hour, minute=0):
    """A local timestamp; the engine reads quiet hours from local time."""
    return datetime(2026, 10, day, hour, minute).timestamp()


def engine(tmp_path, *, now=None, quiet=("00:00", "00:00"), daily_limit=99):
    store = Intents(tmp_path / "intents.json")
    said: list = []
    clock = now if now is not None else (lambda: 100.0)
    motor = Engine(store, speak=said.append, now=clock, quiet=quiet,
                   daily_limit=daily_limit)
    return store, said, motor


def test_a_finished_task_is_kept_and_told(tmp_path):
    store, said, motor = engine(tmp_path)
    motor.note(event("done"))
    assert len(said) == 1
    assert "Сайт готовий." in said[0].text
    assert said[0].person is False
    assert store.alive(now=100.0) == []  # told, so it leaves the held list


def test_a_failed_task_is_told_too(tmp_path):
    _store, said, motor = engine(tmp_path)
    motor.note(event("failed", news="Не вдалося зібрати."))
    assert len(said) == 1 and "Не вдалося" in said[0].text


def test_a_stopped_task_is_not_told(tmp_path):
    store, said, motor = engine(tmp_path)
    motor.note(event("stopped"))
    assert said == [] and store.alive(now=100.0) == []


def test_a_running_task_is_not_told(tmp_path):
    _store, said, motor = engine(tmp_path)
    motor.note(event("running"))
    assert said == []


def test_a_task_with_nothing_to_say_is_not_told(tmp_path):
    store, said, motor = engine(tmp_path)
    motor.note(event("done", news=""))
    assert said == [] and store.alive(now=100.0) == []


def test_an_unrelated_event_is_ignored(tmp_path):
    _store, said, motor = engine(tmp_path)
    motor.note(Ev("phase", {"phase": "idle"}))
    assert said == []


def test_with_no_way_to_speak_the_intent_waits(tmp_path):
    store = Intents(tmp_path / "intents.json")
    Engine(store, now=lambda: 100.0, quiet=("00:00", "00:00"),
           daily_limit=99).note(event("done"))
    assert [i.why for i in store.alive(now=100.0)] == ["task_done"]


@dataclass
class State:
    phase: str = "idle"
    around: dict = field(default_factory=dict)


def test_it_waits_while_the_person_is_away(tmp_path):
    store = Intents(tmp_path / "intents.json")
    said: list = []
    state = State(around={"present": False})
    motor = Engine(store, state=state, speak=said.append, now=lambda: 100.0,
                   quiet=("00:00", "00:00"), daily_limit=99)

    motor.note(event("done"))
    assert said == []  # away: it waits
    assert [i.what for i in store.alive(now=100.0)] == ["Сайт готовий."]

    state.around = {"present": True}
    motor.note(Ev("around", {}))
    assert len(said) == 1 and "Сайт готовий." in said[0].text


def test_it_waits_while_the_brain_is_mid_turn(tmp_path):
    store = Intents(tmp_path / "intents.json")
    said: list = []
    state = State(phase="speaking")
    motor = Engine(store, state=state, speak=said.append, now=lambda: 100.0,
                   quiet=("00:00", "00:00"), daily_limit=99)

    motor.note(event("done"))
    assert said == []  # mid-reply: it waits
    state.phase = "idle"
    motor.note(Ev("phase", {}))
    assert len(said) == 1


def test_several_finished_tasks_are_told_in_one_breath(tmp_path):
    store = Intents(tmp_path / "intents.json")
    said: list = []
    state = State(around={"present": False})
    motor = Engine(store, state=state, speak=said.append, now=lambda: 100.0,
                   quiet=("00:00", "00:00"), daily_limit=99)

    motor.note(event("done", id=1, news="Перше готове."))
    motor.note(event("done", id=2, news="Друге готове."))
    assert said == []  # away: both kept

    state.around = {"present": True}
    motor.note(Ev("around", {}))
    assert len(said) == 1  # one remark, not two
    assert "Перше готове." in said[0].text and "Друге готове." in said[0].text


def test_an_answered_remark_makes_the_next_one_sooner(tmp_path):
    store = Intents(tmp_path / "intents.json")
    said: list = []
    clock = {"t": 0.0}
    motor = Engine(store, speak=said.append, now=lambda: clock["t"],
                   quiet=("00:00", "00:00"), daily_limit=99)

    store.hold("Перше", "news", 2)
    motor.note(Ev("phase", {}))  # told at t=0
    store.hold("Друге", "news", 2)
    motor.note(Ev("phase", {}))
    assert len(said) == 1  # too soon: held

    clock["t"] = 10.0
    motor.note(Ev("heard", {"text": "дякую"}))  # answered, so it grows bolder
    clock["t"] = 200.0
    motor.note(Ev("phase", {}))
    assert len(said) == 2  # sooner than the base gap of four minutes


def test_a_due_reminder_is_said_as_written(tmp_path):
    store, said, motor = engine(tmp_path)
    store.hold("подзвонити мамі", "reminder", 3, not_before=100.0)

    motor.note(Ev("phase", {}))

    assert len(said) == 1
    assert said[0].verbatim is True
    assert said[0].person is False
    assert said[0].text.startswith("Нагадування: подзвонити мамі")
    assert store.alive(now=100.0) == []


def test_a_future_reminder_is_not_said_yet(tmp_path):
    store, said, motor = engine(tmp_path)
    store.hold("подзвонити мамі", "reminder", 3, not_before=101.0)

    motor.note(Ev("phase", {}))

    assert said == []
    assert store.alive(now=100.0) == []
    assert store.alive(now=101.0)[0].status == "held"


def test_a_reminder_and_task_news_are_told_apart(tmp_path):
    store, said, motor = engine(tmp_path)
    store.hold("подзвонити мамі", "reminder", 3, not_before=100.0)

    motor.note(event("done"))

    assert len(said) == 2
    assert said[0].verbatim is True
    assert said[0].text.startswith("Нагадування: подзвонити мамі")
    assert said[1].verbatim is False
    assert said[1].text.startswith(RESULT_PREFIX)
    assert "Сайт готовий." in said[1].text


def test_quiet_hours_hold_news_but_not_reminders(tmp_path):
    clock = {"t": at(1, 23, 0)}
    store, said, motor = engine(tmp_path, now=lambda: clock["t"],
                                quiet=("22:00", "08:00"), daily_limit=5)
    store.hold("подзвонити мамі", "reminder", 3, not_before=clock["t"])
    store.hold("Сайт готовий.", "news", 2)

    motor.note(Ev("phase", {}))

    assert len(said) == 1
    assert said[0].verbatim is True
    assert said[0].text.startswith("Нагадування: подзвонити мамі")
    assert [i.what for i in store.alive(clock["t"])] == ["Сайт готовий."]

    clock["t"] = at(2, 9, 0)
    motor.note(Ev("phase", {}))

    assert len(said) == 2
    assert said[1].text.startswith(RESULT_PREFIX)
    assert "Сайт готовий." in said[1].text
    assert store.alive(clock["t"]) == []


def test_a_quiet_window_wraps_midnight(tmp_path):
    clock = {"t": at(1, 2, 0)}
    store, said, motor = engine(tmp_path, now=lambda: clock["t"],
                                 quiet=("22:00", "08:00"), daily_limit=5)

    store.hold("Сайт готовий.", "news", 2)
    motor.note(Ev("phase", {}))
    assert said == []  # 02:00 is inside the window that began yesterday

    clock["t"] = at(1, 7, 59)
    motor.note(Ev("phase", {}))
    assert said == []

    clock["t"] = at(1, 8, 0)
    motor.note(Ev("phase", {}))
    assert len(said) == 1 and "Сайт готовий." in said[0].text


def test_a_quiet_window_may_lie_inside_one_day(tmp_path):
    clock = {"t": at(1, 13, 30)}
    store, said, motor = engine(tmp_path, now=lambda: clock["t"],
                                 quiet=("13:00", "14:00"))

    store.hold("Сайт готовий.", "news", 2)
    motor.note(Ev("phase", {}))
    assert said == []

    clock["t"] = at(1, 14, 0)
    motor.note(Ev("phase", {}))
    assert len(said) == 1 and "Сайт готовий." in said[0].text


def test_equal_quiet_ends_mean_no_quiet_window(tmp_path):
    _store, said, motor = engine(tmp_path, now=lambda: at(1, 23, 0),
                                 quiet=("22:00", "22:00"))

    motor.note(event("done"))

    assert len(said) == 1 and "Сайт готовий." in said[0].text


def test_the_daily_budget_counts_news_intents_not_messages(tmp_path):
    clock = {"t": at(1, 10, 0)}
    store, said, motor = engine(tmp_path, now=lambda: clock["t"], daily_limit=1)
    store.hold("Перше готове.", "news", 2)
    store.hold("Друге готове.", "news", 2)
    store.hold("Третє готове.", "news", 2)

    motor.note(Ev("phase", {}))

    assert len(said) == 1
    assert "Перше готове." in said[0].text
    assert "Друге готове." not in said[0].text
    assert [i.what for i in store.alive(clock["t"])] == [
        "Друге готове.", "Третє готове."]
    assert all(i.status == "held" for i in store.alive(clock["t"]))

    clock["t"] = at(1, 11, 0)
    restarted = Engine(store, speak=said.append, now=lambda: clock["t"],
                       quiet=("00:00", "00:00"), daily_limit=1)
    restarted.note(Ev("phase", {}))
    assert len(said) == 1  # the budget survived the restart
    assert [i.what for i in store.alive(clock["t"])] == [
        "Друге готове.", "Третє готове."]

    clock["t"] = at(2, 10, 0)
    restarted.note(Ev("phase", {}))

    assert len(said) == 2
    assert "Друге готове." in said[1].text
    assert "Третє готове." not in said[1].text
    assert [i.what for i in store.alive(clock["t"])] == ["Третє готове."]


def test_delivered_at_is_persisted_and_scopes_the_budget(tmp_path):
    path = tmp_path / "intents.json"
    notes = Intents(path)
    today, yesterday = at(2, 10, 0), at(1, 10, 0)

    news = notes.hold("сьогоднішня новина", "news", 2)
    notes.done(news, at=today)
    older = notes.hold("вчорашня новина", "news", 2)
    notes.done(older, at=yesterday)
    reminder = notes.hold("подзвонити мамі", "reminder", 3, not_before=today)
    notes.done(reminder, at=today)
    notes.hold("ще чекає", "task_done", 2)

    assert notes.news_delivered(today) == 1
    assert notes.news_delivered(yesterday) == 1

    saved = {row["id"]: row for row in json.loads(path.read_text(encoding="utf-8"))}
    assert saved[news.id]["delivered_at"] == today

    again = Intents(path)
    assert again.news_delivered(today) == 1
    assert again.news_delivered(yesterday) == 1


def test_reminders_do_not_consume_the_news_budget(tmp_path):
    clock = {"t": at(1, 10, 0)}
    store, said, motor = engine(tmp_path, now=lambda: clock["t"], daily_limit=1)
    store.hold("подзвонити мамі", "reminder", 3, not_before=clock["t"])

    motor.note(Ev("phase", {}))
    assert len(said) == 1 and said[0].verbatim is True

    clock["t"] = at(1, 10, 5)
    motor.note(event("done"))

    assert len(said) == 2
    assert said[1].text.startswith(RESULT_PREFIX)
    assert "Сайт готовий." in said[1].text


def test_a_zero_budget_silences_news_but_not_reminders(tmp_path):
    clock = {"t": at(1, 10, 0)}
    store, said, motor = engine(tmp_path, now=lambda: clock["t"], daily_limit=0)

    store.hold("Сайт готовий.", "news", 2)
    motor.note(Ev("phase", {}))
    assert said == []
    assert [i.what for i in store.alive(clock["t"])] == ["Сайт готовий."]

    store.hold("подзвонити мамі", "reminder", 3, not_before=clock["t"])
    motor.note(Ev("phase", {}))

    assert len(said) == 1 and said[0].verbatim is True
    assert [i.what for i in store.alive(clock["t"])] == ["Сайт готовий."]


async def test_the_engine_wakes_on_its_own_timer(tmp_path):
    from heare.core.state import State

    store = Intents(tmp_path / "intents.json")
    store.hold("подзвонити мамі", "reminder", 3, not_before=0.0)
    said: list = []
    motor = Engine(store, state=State(), speak=said.append, tick=0.01,
                   quiet=("00:00", "00:00"), daily_limit=99)

    task = asyncio.create_task(motor.run())
    try:
        await asyncio.sleep(0.05)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert [m.text for m in said] == ["Нагадування: подзвонити мамі"]


def test_the_result_of_asked_for_work_is_told_whatever_the_budget_hour_or_gap(tmp_path):
    clock = {"t": at(1, 23, 0)}
    store, said, motor = engine(tmp_path, now=lambda: clock["t"],
                                quiet=("22:00", "08:00"), daily_limit=0)

    motor.note(event("done", id=1, news="Перше готове."))
    clock["t"] = at(1, 23, 1)  # well inside the gap after the first
    motor.note(event("failed", id=2, news="Друге не вийшло."))

    assert [m.text.splitlines()[-1] for m in said] == [
        "— Перше готове.", "— Друге не вийшло."]
    assert store.news_delivered(clock["t"]) == 0  # answers spend no budget
