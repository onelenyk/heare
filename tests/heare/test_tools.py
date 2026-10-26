"""The toolbox: which abilities are offered, and what each one says."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

from heare.core.forms import Task
from heare.core.state import State
from heare.engine.store import Intents
from heare.language import uk
from heare.tools import REMIND_GRACE_S, Toolbox
from tests.heare.fakes import Notebook


def _toolbox(
    *,
    memory: Notebook | None = None,
    state: State | None = None,
    has_executors: bool = True,
    stop=lambda: 0,
    delegate=None,
    reminders: Intents | None = None,
    now=lambda: 100.0,
) -> Toolbox:
    return Toolbox(
        memory=memory,
        state=state or State(),
        delegate=delegate or (lambda request: Task(1, request, "")),
        stop=stop,
        words=uk,
        has_executors=lambda: has_executors,
        reminders=reminders,
        now=now,
    )


def test_recall_is_offered_only_with_memory():
    assert "recall" in _toolbox(memory=Notebook()).verbs()
    assert "recall" not in _toolbox(memory=None).verbs()


def test_delegate_is_offered_only_with_executors():
    assert "delegate" in _toolbox(has_executors=True).verbs()
    assert "delegate" not in _toolbox(has_executors=False).verbs()


def test_stop_work_is_offered_only_with_a_running_task():
    running = State()
    running.tasks[1] = Task(1, "відкрий файл", "worker")
    done = State()
    done.tasks[1] = Task(1, "відкрий файл", "worker", status="done")

    assert "stop_work" in _toolbox(state=running).verbs()
    assert "stop_work" not in _toolbox(state=done).verbs()


def test_acknowledging_names_the_verbs_that_only_acknowledge():
    assert _toolbox().acknowledging() == {
        "delegate", "stop_work", "remind", "cancel_reminder",
    }


def test_read_only_names_the_verbs_that_change_nothing(tmp_path):
    notes = Intents(tmp_path / "intents.json")
    box = _toolbox(memory=Notebook(), reminders=notes)
    assert box.read_only("recall") is True
    assert box.read_only("list_reminders") is True
    assert box.read_only("cancel_reminder") is False
    assert box.read_only("delegate") is False
    assert box.read_only("remind") is False


def test_remind_is_offered_only_with_intents(tmp_path):
    assert "remind" not in _toolbox().verbs()
    notes = Intents(tmp_path / "intents.json")
    assert "remind" in _toolbox(reminders=notes).verbs()


def test_reminder_management_is_offered_only_with_intents(tmp_path):
    assert "list_reminders" not in _toolbox().verbs()
    assert "cancel_reminder" not in _toolbox().verbs()
    notes = Intents(tmp_path / "intents.json")
    verbs = _toolbox(reminders=notes).verbs()
    assert "list_reminders" in verbs
    assert "cancel_reminder" in verbs


async def test_recall_returns_the_memorys_answer():
    notebook = Notebook(found="Ти казав про це вчора.")

    spoken = await _toolbox(memory=notebook)._run_recall(
        {"query": "файл", "when": "вчора"}
    )

    assert spoken == "Ти казав про це вчора."
    assert notebook.recalled == [("файл", "вчора")]


async def test_delegate_calls_the_callback_and_acknowledges():
    asked: list[str] = []

    spoken = await _toolbox(
        delegate=lambda request, follows: asked.append(request) or Task(1, request, "")
    )._run_delegate({"task": "перевір пошту"})

    assert spoken == "Беру."
    assert asked == ["перевір пошту"]


async def test_delegate_passes_on_whether_the_work_goes_on_from_the_last():
    asked: list[tuple[str, bool]] = []
    box = _toolbox(delegate=lambda request, follows: asked.append((request, follows))
                   or Task(1, request, ""))

    await box._run_delegate({"task": "видали його", "continues": True})
    await box._run_delegate({"task": "список покупок"})
    await box._run_delegate({"task": "ще щось", "continues": "так"})

    assert asked == [("видали його", True), ("список покупок", False),
                     ("ще щось", False)]


async def test_a_duplicate_running_request_is_not_started_again():
    asked: list[str] = []
    state = State()
    state.tasks[1] = Task(1, "відкрий файл", "worker")
    box = _toolbox(
        state=state,
        delegate=lambda request, follows: asked.append(request) or Task(2, request, ""),
    )

    spoken = await box._run_delegate({"task": "Відкрий файл"})

    assert spoken == "Це вже в роботі."
    assert asked == []


async def test_stop_work_reports_what_it_stopped():
    assert await _toolbox(stop=lambda: 2)._run_stop({}) == "Зупинив."
    assert await _toolbox(stop=lambda: 0)._run_stop({}) == "Нічого не було запущено."


async def test_remind_stores_a_one_off_due_later(tmp_path):
    notes = Intents(tmp_path / "intents.json")
    box = _toolbox(reminders=notes, now=lambda: 100.0)

    spoken = await box._run_remind(
        {"text": "подзвонити мамі", "in_seconds": 600}
    )

    assert spoken == "Добре, нагадаю."
    [held] = notes.alive(now=700.0)
    assert held.why == "reminder"
    assert held.not_before == 700.0
    assert held.until == 700.0 + REMIND_GRACE_S


async def test_remind_uses_an_absolute_time_with_an_offset(tmp_path):
    notes = Intents(tmp_path / "intents.json")
    at = "2026-10-23T15:00:00+03:00"
    due = datetime.fromisoformat(at).timestamp()
    box = _toolbox(reminders=notes, now=lambda: due - 600)

    spoken = await box._run_remind({"text": "подзвонити мамі", "at": at})

    assert spoken == "Добре, нагадаю."
    [held] = notes.alive(now=due)
    assert held.not_before == due
    assert held.until == due + REMIND_GRACE_S


async def test_remind_reads_a_naive_time_as_local(tmp_path):
    notes = Intents(tmp_path / "intents.json")
    now = time.time()
    at = (datetime.now() + timedelta(hours=1)).replace(
        microsecond=0
    ).isoformat(timespec="seconds")
    parsed = datetime.fromisoformat(at)
    expected = time.mktime(parsed.timetuple()) + parsed.microsecond / 1_000_000
    box = _toolbox(reminders=notes, now=lambda: now)

    spoken = await box._run_remind({"text": "подзвонити мамі", "at": at})

    assert spoken == "Добре, нагадаю."
    [held] = notes.reminders(now=now)
    assert held.not_before == expected
    assert held.not_before > now


async def test_remind_prefers_at_over_in_seconds(tmp_path):
    notes = Intents(tmp_path / "intents.json")
    at = "2026-12-01T09:00:00+02:00"
    due = datetime.fromisoformat(at).timestamp()
    box = _toolbox(reminders=notes, now=lambda: due - 60)

    spoken = await box._run_remind(
        {"text": "подзвонити мамі", "at": at, "in_seconds": 600}
    )

    assert spoken == "Добре, нагадаю."
    [held] = notes.alive(now=due)
    assert held.not_before == due


async def test_a_past_at_stores_nothing(tmp_path):
    notes = Intents(tmp_path / "intents.json")
    box = _toolbox(reminders=notes, now=lambda: 1_600_000_000.0)

    spoken = await box._run_remind(
        {"text": "подзвонити мамі", "at": "2020-01-01T00:00:00+00:00"}
    )

    assert spoken == "Це вже минуло."
    assert notes.reminders(now=0.0) == []


async def test_an_invalid_at_stores_nothing(tmp_path):
    notes = Intents(tmp_path / "intents.json")
    box = _toolbox(reminders=notes, now=lambda: 100.0)

    spoken = await box._run_remind({"text": "подзвонити мамі", "at": "завтра"})

    assert spoken == "Скажи, коли нагадати."
    assert notes.reminders(now=0.0) == []


async def test_list_reminders_is_empty_when_nothing_is_held(tmp_path):
    box = _toolbox(reminders=Intents(tmp_path / "intents.json"))

    assert await box._run_list_reminders({}) == "Нагадувань немає."


async def test_list_reminders_shows_ids_text_and_when(tmp_path):
    notes = Intents(tmp_path / "intents.json")
    untimed = notes.hold("згадати все", "reminder", 3)
    due = notes.hold("випити воду", "reminder", 3, not_before=50.0)
    future = notes.hold("подзвонити мамі", "reminder", 3, not_before=700.0)
    box = _toolbox(reminders=notes, now=lambda: 100.0)

    assert await box._run_list_reminders({}) == (
        "Нагадування:\n"
        f"{untimed.id}. згадати все — без часу\n"
        f"{due.id}. випити воду — вже час\n"
        f"{future.id}. подзвонити мамі — через 10 хв"
    )


async def test_cancel_reminder_removes_it_and_reports_missing(tmp_path):
    notes = Intents(tmp_path / "intents.json")
    held = notes.hold("подзвонити мамі", "reminder", 3, not_before=700.0)
    box = _toolbox(reminders=notes, now=lambda: 100.0)

    assert await box._run_cancel_reminder({"id": held.id}) == "Скасував."
    assert notes.reminders(now=100.0) == []
    assert await box._run_cancel_reminder({"id": held.id}) == (
        "Не знайшов такого нагадування."
    )
    assert await box._run_cancel_reminder({}) == "Скажи, яке нагадування скасувати."
    assert await box._run_cancel_reminder({"id": "скоро"}) == (
        "Скажи, яке нагадування скасувати."
    )


@pytest.mark.parametrize(
    ("arguments", "spoken"),
    [
        ({"text": "", "in_seconds": 600}, "Що саме нагадати?"),
        ({"text": "подзвонити", "in_seconds": "soon"}, "Скажи, коли нагадати."),
        ({"text": "подзвонити"}, "Скажи, коли нагадати."),
        ({"text": "подзвонити", "at": "2030-01-01T00:00:00+00:00"},
         "Так далеко я не нагадую."),
        ({"text": "подзвонити", "in_seconds": 0}, "Це вже минуло."),
        ({"text": "подзвонити", "in_seconds": -5}, "Це вже минуло."),
        ({"text": "подзвонити", "in_seconds": 30 * 24 * 3600 + 1},
         "Так далеко я не нагадую."),
    ],
)
async def test_a_bad_reminder_stores_nothing(tmp_path, arguments, spoken):
    notes = Intents(tmp_path / "intents.json")
    box = _toolbox(reminders=notes)

    assert await box._run_remind(arguments) == spoken
    assert notes.alive(now=10**12) == []


def test_delegate_names_the_hands_that_are_here():
    box = Toolbox(memory=None, state=State(), delegate=lambda r, c: Task(1, r, ""),
                  stop=lambda: 0, words=uk, has_executors=lambda: True,
                  hands=lambda: [("native-hands", "панель на столі: світло, піни")])
    described = box.verbs()["delegate"][0]["function"]["description"]
    assert "- native-hands: панель на столі: світло, піни" in described
