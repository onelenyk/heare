"""Memory, end to end: real stores, a restart, and what the model sees.

These tests build the app's memory stack over a temporary root — a real
``Book`` over SQLite, a real ``Journal`` over JSONL, a real ``Profile``
over Markdown, and the core in front of them — then restart it over the
same files. ``Book``, ``Journal`` and ``Core`` are the production ones;
only the model and the parts are stand-ins.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from heare.core import Core, Message
from heare.language import uk
from heare.memory import search
from heare.memory.book import Book
from heare.memory.journal import Journal
from heare.memory.profile import Profile
from heare.tools import Toolbox

from tests.heare.fakes import Line, Page, Script


async def build_rig(root: Path, model: Script) -> Rig:
    """A live stack over ``root``: the same files a restart reopens."""
    journal = Journal(root / "events")
    book = Book(root / "heare.db", profile_dir=root / "memory", journal=journal)

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
    ))
    core.hydrate(journal.tail_messages())
    task = asyncio.create_task(core.run())
    await asyncio.sleep(0)
    return Rig(root, model, journal, book, core, line, page, task)


@dataclass
class Rig:
    root: Path
    model: Script
    journal: Journal
    book: Book
    core: Core
    line: Line
    page: Page
    _task: asyncio.Task

    async def say(self, text: str, timeout: float = 3.0) -> None:
        """Speak a turn and wait until it has actually been answered."""
        self.core.hear(Message(text=text, channel="text"))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            await asyncio.sleep(0.02)
            if self.core.settled:
                return
        raise AssertionError("the core never settled")

    async def close(self) -> None:
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self.book.close()

    async def restart(self, model: Script) -> Rig:
        """Close this rig, then open another over the same files."""
        await self.close()
        return await build_rig(self.root, model)


async def test_a_profile_fact_is_in_the_prompt_after_a_restart(tmp_path):
    Profile(tmp_path / "memory").write("user", "Мене звати Назар.")

    rig = await build_rig(tmp_path, Script())
    try:
        rig.model.will_say("Привіт.")
        await rig.say("Привіт")

        rig = await rig.restart(Script())
        rig.model.will_say("Тебе звати Назар.")
        await rig.say("Як мене звати?")

        asked = rig.model.asked[0]
        assert "Назар" in asked[0]["content"]
        turns = [(m["role"], m["content"].strip()) for m in asked[1:]]
        assert ("user", "Привіт") in turns
        assert ("assistant", "Привіт.") in turns
        assert turns.index(("user", "Привіт")) < turns.index(
            ("user", "Як мене звати?")
        )
    finally:
        await rig.close()


async def test_a_recall_cue_finds_what_was_said_before_the_restart(
    tmp_path, monkeypatch
):
    # A turn made seconds ago is not yet "a memory" to the real search:
    # the app waits 90 seconds so a question cannot answer itself. The
    # restart here is instant, so collapse that window and keep the rest
    # of the recall path real — FTS over SQLite, the same query, the
    # same spoken answer.
    monkeypatch.setattr(search, "NOT_YET_A_MEMORY_S", 0.0)

    rig = await build_rig(tmp_path, Script())
    try:
        rig.model.will_say("Запам'ятав.")
        await rig.say("Мій колір — синій.")

        rig = await rig.restart(Script())
        rig.model.will_say("Ти казав, що синій.")
        await rig.say("Що я казав про колір?")

        system = rig.model.asked[0][0]["content"]
        assert "[знайдене в памʼяті" in system
        assert "синій" in system
        assert "Ти казав, що синій." in rig.page.said[-1]
    finally:
        await rig.close()


async def test_forgetting_removes_the_turn_and_clearing_the_profile_removes_the_fact(
    tmp_path,
):
    memory = tmp_path / "memory"
    Profile(memory).write("user", "Мене звати Назар.")

    rig = await build_rig(tmp_path, Script())
    try:
        rig.model.will_say("Добре.")
        await rig.say("Запам'ятай це.")
        ids = [
            event["id"] for event in rig.journal.events()
            if event.get("kind") in ("heard", "said")
        ]
        rig.journal.forget(ids)
        assert rig.journal.tail_messages() == []

        Profile(memory).write("user", "")
        rig = await rig.restart(Script())
        rig.model.will_say("Не знаю.")
        await rig.say("Привіт")

        assert all(
            m["content"].strip() != "Запам'ятай це." for m in rig.core.history
        )
        system = rig.model.asked[0][0]["content"]
        assert "Назар" not in system
        assert "Запам'ятай це" not in system
    finally:
        await rig.close()


async def test_the_journal_and_the_book_see_the_same_turn(tmp_path):
    rig = await build_rig(tmp_path, Script())
    try:
        rig.model.will_say("Вітаю.")
        await rig.say("Привіт")

        tail = rig.journal.tail_messages()
        assert [(m["role"], m["content"].strip()) for m in tail] == [
            ("user", "Привіт"),
            ("assistant", "Вітаю."),
        ]

        exchanges = await rig.book.recent(5)
        assert len(exchanges) == 1
        assert exchanges[0]["user"].strip() == "Привіт"
        assert exchanges[0]["agent"].strip() == "Вітаю."
    finally:
        await rig.close()
