"""The toolbox: which abilities are offered, and what each one says."""

from __future__ import annotations

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
    intents: Intents | None = None,
    now=lambda: 100.0,
) -> Toolbox:
    return Toolbox(
        memory=memory,
        state=state or State(),
        delegate=delegate or (lambda request: Task(1, request, "")),
        stop=stop,
        words=uk,
        has_executors=lambda: has_executors,
        intents=intents,
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
    assert _toolbox().acknowledging() == {"delegate", "stop_work", "remind"}


def test_only_recall_is_read_only():
    box = _toolbox(memory=Notebook())
    assert box.read_only("recall") is True
    assert box.read_only("delegate") is False
    assert box.read_only("remind") is False


def test_remind_is_offered_only_with_intents(tmp_path):
    assert "remind" not in _toolbox().verbs()
    notes = Intents(tmp_path / "intents.json")
    assert "remind" in _toolbox(intents=notes).verbs()


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
        delegate=lambda request: asked.append(request) or Task(1, request, "")
    )._run_delegate({"task": "перевір пошту"})

    assert spoken == "Беру."
    assert asked == ["перевір пошту"]


async def test_a_duplicate_running_request_is_not_started_again():
    asked: list[str] = []
    state = State()
    state.tasks[1] = Task(1, "відкрий файл", "worker")
    box = _toolbox(
        state=state,
        delegate=lambda request: asked.append(request) or Task(2, request, ""),
    )

    spoken = await box._run_delegate({"task": "Відкрий файл"})

    assert spoken == "Це вже в роботі."
    assert asked == []


async def test_stop_work_reports_what_it_stopped():
    assert await _toolbox(stop=lambda: 2)._run_stop({}) == "Зупинив."
    assert await _toolbox(stop=lambda: 0)._run_stop({}) == "Нічого не було запущено."


async def test_remind_stores_a_one_off_due_later(tmp_path):
    notes = Intents(tmp_path / "intents.json")
    box = _toolbox(intents=notes, now=lambda: 100.0)

    spoken = await box._run_remind(
        {"text": "подзвонити мамі", "in_seconds": 600}
    )

    assert spoken == "Добре, нагадаю."
    [held] = notes.alive(now=700.0)
    assert held.why == "reminder"
    assert held.not_before == 700.0
    assert held.until == 700.0 + REMIND_GRACE_S


@pytest.mark.parametrize(
    ("arguments", "spoken"),
    [
        ({"text": "", "in_seconds": 600}, "Що саме нагадати?"),
        ({"text": "подзвонити", "in_seconds": "soon"}, "Скажи, коли нагадати."),
        ({"text": "подзвонити", "in_seconds": 0}, "Це вже минуло."),
        ({"text": "подзвонити", "in_seconds": -5}, "Це вже минуло."),
        ({"text": "подзвонити", "in_seconds": 30 * 24 * 3600 + 1},
         "Так далеко я не нагадую."),
    ],
)
async def test_a_bad_reminder_stores_nothing(tmp_path, arguments, spoken):
    notes = Intents(tmp_path / "intents.json")
    box = _toolbox(intents=notes)

    assert await box._run_remind(arguments) == spoken
    assert notes.alive(now=10**12) == []
