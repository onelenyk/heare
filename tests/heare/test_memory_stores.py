"""One turn has a single write path and reaches both stores.

``Book.keep`` writes SQLite and the journal; ``Journal.run`` only mirrors
system events, so it must never write the same turn again. SQLite remains
a second store until Phase 2.
"""

from __future__ import annotations

import asyncio

from heare.core import Core, Message
from heare.language import uk
from heare.memory.book import Book
from heare.memory.journal import Journal
from tests.heare.fakes import Line, Page, Script


async def test_one_turn_reaches_both_stores(tmp_path):
    journal = Journal(tmp_path / "events")
    book = Book(
        tmp_path / "heare.db", profile_dir=tmp_path / "memory", journal=journal
    )
    model, line, page = Script(), Line(), Page()
    core = Core(model, memory=book, names=frozenset({"дока"}), words=uk)
    core.add_input(line)
    core.add_output(page)
    model.will_say("вітаю")

    running = [
        asyncio.create_task(core.run()),
        asyncio.create_task(journal.run(core.state)),
    ]
    try:
        await asyncio.sleep(0)
        core.hear(Message(text="привіт", channel="text"))
        for _ in range(200):
            if len(journal.tail_messages()) == 2:
                break
            await asyncio.sleep(0.01)

        tail = journal.tail_messages()
        assert [(m["role"], m["content"].strip()) for m in tail] == [
            ("user", "привіт"),
            ("assistant", "вітаю"),
        ]
        exchanges = await book.recent(5)
        assert len(exchanges) == 1
        assert exchanges[0]["user"].strip() == "привіт"
        assert exchanges[0]["agent"].strip() == "вітаю"
    finally:
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        book.close()
