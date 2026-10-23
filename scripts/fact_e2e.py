"""A manual live check of the fact loop: hear, consolidate, confirm, restart.

Paid and text-only: it builds the real ``Chat`` from ``Settings`` and makes
two real model calls. Everything else is the production stack — ``Core``,
``Book``, ``Journal``, ``SQLiteFacts``, ``consolidate`` — over a temporary
state root, so ``~/.heare`` is read for settings and keys but never written.

The first reply may say anything; the fact comes from the journal's
``heard`` event, not from what the model answered. The check is the second
model call: after a restart over the same files and a confirmed fact, the
reply to «Як мене звати?» must contain the name.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import tempfile
import time
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

from heare.config import Settings
from heare.core import Core, Message
from heare.language import uk
from heare.memory.book import Book
from heare.memory.consolidate import consolidate
from heare.memory.contracts import PENDING
from heare.memory.facts import SQLiteFacts
from heare.memory.journal import Journal
from heare.model.chat import Chat

PROMPT = "Ти heare. Відповідай коротко, українською, простою прозою."
NAME = "Олена"


class Collector:
    name = "text"
    abilities: frozenset[str] = frozenset()

    def __init__(self) -> None:
        self._parts: list[str] = []

    @property
    def speaking(self) -> bool:
        return False

    @property
    def text(self) -> str:
        return "".join(self._parts).strip()

    async def say(self, pieces: AsyncIterator[str]) -> str:
        async for piece in pieces:
            self._parts.append(piece)
        return "done"

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass

    def stop(self) -> None:
        pass


def _build(root: Path, chat: Chat) -> tuple[Journal, SQLiteFacts, Book, Core]:
    journal = Journal(root / "events")
    facts = SQLiteFacts(root / "heare.db")
    book = Book(
        root / "heare.db",
        profile_dir=root / "memory",
        journal=journal,
        facts=facts,
    )

    async def prompt() -> str:
        return PROMPT

    core = Core(
        chat,
        memory=book,
        names=frozenset({"heare"}),
        words=uk,
        prompt=prompt,
    )
    return journal, facts, book, core


async def _settle(core: Core, task: asyncio.Task, timeout: float = 120.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if core.settled:
            return
        if task.done():
            task.result()
            raise RuntimeError("the core stopped before it settled")
        await asyncio.sleep(0.05)
    raise TimeoutError("the core never settled")


async def _stop(task: asyncio.Task, book: Book) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    book.close()


async def _run(root: Path) -> tuple[bool, str]:
    chat = Chat(Settings())

    journal, facts, book, core = _build(root, chat)
    out = Collector()
    core.add_output(out)
    task = asyncio.create_task(core.run())
    await asyncio.sleep(0)
    try:
        core.hear(Message(text=f"Мене звати {NAME}.", channel="text"))
        await _settle(core, task)
    finally:
        await _stop(task, book)

    report = consolidate(facts, journal, now=time.time())
    pending = [
        fact for fact in facts.current(include_pending=True)
        if fact.predicate == "name"
        and fact.status == PENDING
        and NAME in fact.value
    ]
    if not pending:
        print(f"no pending name fact (consolidation: {report})")
        for fact in facts.current(include_pending=True):
            print(f"  {fact.predicate}: {fact.value} [{fact.status}]")
        return False, ""

    ident = pending[0].id
    if ident is None or not facts.confirm(ident):
        return False, "the pending name fact could not be confirmed"

    journal, _, book, core = _build(root, chat)
    core.hydrate(journal.tail_messages())
    out = Collector()
    core.add_output(out)
    task = asyncio.create_task(core.run())
    await asyncio.sleep(0)
    try:
        core.hear(Message(text="Як мене звати?", channel="text"))
        await _settle(core, task)
    finally:
        await _stop(task, book)

    reply = out.text
    return NAME.casefold() in reply.casefold(), reply


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep",
        action="store_true",
        help="keep the temporary state root and print its path",
    )
    args = parser.parse_args(argv)

    root = Path(tempfile.mkdtemp(prefix="heare-fact-e2e-"))
    try:
        ok, reply = asyncio.run(_run(root))
    finally:
        if args.keep:
            print(f"kept: {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)

    print(f"{'PASS' if ok else 'FAIL'} — {reply}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
