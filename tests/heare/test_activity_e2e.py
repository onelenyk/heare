"""Reminders, end to end: the real stores, engine and core; fake parts.

Everything below the model is the production stack over a temporary
root — ``Book``, ``Journal``, ``Intents``, ``Toolbox``, ``Engine`` and
``Core`` — with only the model and the parts standing in. The engine is
not left running: each delivery is a ``note`` the test makes itself, so
the scenario never waits on a tick and never touches the clock's zone.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from heare.core import Core, Event, Message
from heare.engine import Engine, Intents
from heare.language import uk
from heare.memory.book import Book
from heare.memory.journal import Journal
from heare.tools import Toolbox

from tests.heare.fakes import Line, Page, Says, Script

NOON = datetime(2026, 10, 22, 12, 0).timestamp()


@dataclass
class Rig:
    model: Script
    journal: Journal
    book: Book
    intents: Intents
    engine: Engine
    core: Core
    page: Page
    clock: dict[str, float]
    _task: asyncio.Task

    async def settled(self, timeout: float = 3.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            await asyncio.sleep(0.02)
            if self.core.settled:
                return
        raise AssertionError("the core never settled")

    async def say(self, text: str, timeout: float = 3.0) -> None:
        self.core.hear(Message(text=text, channel="text"))
        await self.settled(timeout)

    async def deliver(self, timeout: float = 3.0) -> None:
        """Hand the engine the idle phase, as its own reader would."""
        self.engine.note(Event("phase", {"phase": "idle"}, self.clock["t"]))
        await self.settled(timeout)

    async def close(self) -> None:
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self.book.close()


def build_rig(root: Path) -> Rig:
    clock = {"t": NOON}
    journal = Journal(root / "events")
    book = Book(root / "heare.db", profile_dir=root / "memory", journal=journal)
    intents = Intents(root / "intents.json")
    model = Script()

    async def prompt() -> str:
        return "Ти heare."

    core = Core(
        model, memory=book, names=frozenset({"дока"}), words=uk, prompt=prompt
    )
    line, page = Line(), Page()
    core.add_input(line)
    core.add_output(page)
    core.attach_tools(Toolbox(
        memory=book, state=core.state, delegate=core.delegate,
        stop=core.stop_all, words=uk, has_executors=lambda: False,
        reminders=intents, now=lambda: clock["t"],
    ))
    engine = Engine(
        intents, state=core.state, speak=core.hear, now=lambda: clock["t"],
        quiet=("00:00", "00:00"), daily_limit=99,
    )
    task = asyncio.create_task(core.run())
    return Rig(model, journal, book, intents, engine, core, page, clock, task)


async def test_a_reminder_is_set_listed_cancelled_and_delivered(tmp_path):
    rig = build_rig(tmp_path)
    try:
        # 1. The model calls remind; the toolbox holds it through Intents.
        rig.model.will_say(Says(text="", calls=(
            ("remind", {"text": "подзвонити мамі", "in_seconds": 600}),
        )))
        await rig.say("Нагадай через 10 хвилин подзвонити мамі")

        assert rig.page.said[-1].strip() == "Добре, нагадаю."
        [reminder] = rig.intents.reminders(rig.clock["t"])
        assert reminder.what == "подзвонити мамі"
        assert reminder.not_before == rig.clock["t"] + 600

        # 2. list_reminders is read-only: the core runs it and asks again
        #    with the tool turn in front of the model, all in one say.
        rig.model.will_say(
            Says(calls=(("list_reminders", {}),)),
            Says("У тебе одне нагадування."),
        )
        await rig.say("Що ти тримаєш у голові?")

        assert len(rig.model.asked) == 3
        follow_up = rig.model.asked[-1]
        tool = next(m for m in follow_up if m["role"] == "tool")
        assert "подзвонити мамі" in tool["content"]
        assert str(reminder.id) in tool["content"]
        assert rig.page.said[-1].strip() == "У тебе одне нагадування."

        # 3. Cancelling is a side effect, spoken as the verb's own answer.
        rig.model.will_say(Says(calls=(
            ("cancel_reminder", {"id": reminder.id}),
        )))
        await rig.say("Скасуй це нагадування")

        assert rig.page.said[-1].strip() == "Скасував."
        assert rig.intents.reminders(rig.clock["t"]) == []

        # 4. A second reminder, this time due in five minutes.
        rig.model.will_say(Says(text="", calls=(
            ("remind", {"text": "випити води", "in_seconds": 300}),
        )))
        await rig.say("Нагадай через 5 хвилин випити води")

        [water] = rig.intents.reminders(rig.clock["t"])
        assert water.what == "випити води"

        # 5. Let five minutes pass and hand the engine an idle phase: it
        #    speaks the reminder verbatim and the journal keeps those words.
        rig.clock["t"] += 300
        await rig.deliver()

        assert rig.page.said[-1].strip() == "Нагадування: випити води"
        assert rig.intents.reminders(rig.clock["t"]) == []
        assert rig.journal.tail_messages()[-1]["content"] == (
            "Нагадування: випити води"
        )
    finally:
        await rig.close()
