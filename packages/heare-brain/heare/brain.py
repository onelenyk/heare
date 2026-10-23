"""The brain: model, memory, delegator, executors, engine — and itself.

Runs on its own, with no body:

    uv run python -m heare.brain

A body is not part of it. The brain opens its door — the API in ``api/`` —
and a body connects to that door. That is how a person speaks to the brain
and hears it back; the brain itself has no ears and no mouth.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time as clock
from datetime import date, datetime, time, timedelta
from functools import partial
from pathlib import Path
from typing import Any

from heare import lifeline
from heare.prompt import budget, persona
from heare.api import pair as codes
from heare.config import Settings
from heare.core import Core
from heare.engine import Engine, Intents
from heare.language import uk
from heare.memory.contracts import ReviewReport

logger = logging.getLogger("heare.brain")

# How often a pass turns heard words into proposed facts. The first one
# comes after the interval, never during startup.
CONSOLIDATE_EVERY_S = 5 * 60.0

# A failed daily review is retried rather than skipped until tomorrow.
REVIEW_RETRY_S = 15 * 60.0

# How many missed days a single run will catch up on. A week is enough to
# survive a holiday without turning one startup into a month of model
# calls; older days stay unreviewed by choice.
REVIEW_CATCH_UP_DAYS = 7


def next_review_at(now: float, hour: int) -> float:
    """The next local ``hour:00``; today's if it is still ahead."""
    hour = int(hour) % 24
    moment = datetime.fromtimestamp(now)
    when = datetime.combine(moment.date(), time(hour=hour))
    if when.timestamp() <= now:
        when += timedelta(days=1)
    return when.timestamp()


def model_info(settings: Settings) -> dict:
    """Which model is answering — and whether it is the one that was chosen.

    A chosen provider with no key falls back to another, and that has to be
    visible: the old dashboard showed a choice that changed nothing.
    """
    from heare.model.llm import resolve_llm
    from heare.model.providers import PROVIDERS

    wanted = str(settings.llm_provider or "deepseek").lower()
    logging.getLogger("heare.model.llm").disabled = True
    try:
        cfg = resolve_llm(settings)
    except RuntimeError as exc:
        return {"wanted": wanted, "error": str(exc), "name": persona.name()}
    finally:
        logging.getLogger("heare.model.llm").disabled = False
    return {
        "provider": cfg.provider, "model": cfg.model, "wanted": wanted,
        "fallback": cfg.provider != wanted, "providers": sorted(PROVIDERS),
        "name": persona.name(),
    }


class Brain:
    """Everything that thinks, and runs on its own."""

    def __init__(self, settings: Settings | None = None) -> None:
        from heare.memory.book import Book
        from heare.memory.facts import SQLiteFacts
        from heare.memory.journal import Journal
        from heare.model.chat import Chat
        from heare.model.delegate import Dispatcher
        from heare.tools import Toolbox

        self.settings = settings or Settings()
        names = persona.names()
        self.journal = Journal(self.settings.events_dir)
        self.facts = SQLiteFacts(self.settings.db_path)
        self.intents = Intents(self.settings.intents_path)
        self.core = Core(
            Chat(self.settings),
            memory=Book(
                self.settings.db_path, names=sorted(names),
                profile_dir=self.settings.memory_dir, journal=self.journal,
                facts=self.facts,
            ),
            delegator=Dispatcher(self.settings), prompt=persona.prompt,
            names=names, judge=persona.JUDGE, words=uk,
            fit=partial(budget.fit, limit=int(self.settings.context_tokens)),
        )
        self.core.attach_tools(Toolbox(
            memory=self.core.memory, state=self.core.state,
            delegate=self.core.delegate, stop=self.core.stop_all,
            words=self.core.words,
            has_executors=lambda: bool(self.core.executors),
            reminders=self.intents,
        ))
        # Hands are a body: they register over the door and are added then.
        # The brain itself holds no agent's process.
        self.engine = Engine(
            self.intents, state=self.core.state, speak=self.core.hear,
            quiet=(self.settings.quiet_from, self.settings.quiet_to),
            daily_limit=int(self.settings.unsolicited_per_day),
        )
        self._runner: Any = None

    async def _consolidate(self) -> None:
        from heare.memory.consolidate import consolidate

        while True:
            await asyncio.sleep(CONSOLIDATE_EVERY_S)
            try:
                report = await asyncio.to_thread(
                    consolidate, self.facts, self.journal,
                    state_path=Path(self.settings.memory_dir)
                    / "consolidate.json",
                )
                if report.added:
                    logger.info("consolidation added %d fact(s)", report.added)
            except Exception:
                logger.exception("consolidation failed (non-fatal)")

    async def review_now(self, day: date | None = None) -> ReviewReport:
        from heare.memory.review import review_day

        if day is None:
            day = date.today() - timedelta(days=1)
        return await review_day(
            self.facts, self.journal, self.core.model, day=day
        )

    async def _review(self) -> None:
        from heare.memory.review import last_reviewed, mark_reviewed

        while True:
            if not bool(self.settings.review_enabled):
                return
            memory_dir = Path(self.settings.memory_dir)
            last = last_reviewed(memory_dir)
            yesterday = date.today() - timedelta(days=1)
            if last is None:
                target = yesterday
            else:
                if (yesterday - last).days > REVIEW_CATCH_UP_DAYS:
                    last = yesterday - timedelta(days=REVIEW_CATCH_UP_DAYS)
                target = last + timedelta(days=1)
            if target > yesterday:
                when = next_review_at(
                    clock.time(), int(self.settings.review_hour)
                )
                await asyncio.sleep(max(1.0, when - clock.time()))
                continue
            report = await self.review_now(target)
            if report.error:
                logger.warning(
                    "daily review for %s failed: %s", target, report.error
                )
                await asyncio.sleep(REVIEW_RETRY_S)
                continue
            if report.added:
                logger.info(
                    "daily review for %s added %d fact(s)",
                    target, report.added,
                )
            mark_reviewed(memory_dir, target)

    async def run(self) -> None:
        """Live until cancelled: open the door, answer, hold intents."""
        from heare.api.server import serve
        from heare.reach.remote import RemoteExecutor

        self.core.hydrate(self.journal.tail_messages())
        self._runner = await serve(
            self.core, self.settings, lambda: model_info(self.settings),
            make_executor=lambda name, about: RemoteExecutor(name, about),
        )
        print(f"мозок на http://127.0.0.1:{self.settings.api_port}/", file=sys.stderr)
        await asyncio.gather(
            self.core.run(), self.engine.run(), self.journal.run(self.core.state),
            self._consolidate(), self._review(),
        )


def main() -> int:
    parser = argparse.ArgumentParser(prog="heare-brain")
    parser.add_argument(
        "command", nargs="?", default="run",
        choices=["run", "code", "review"],
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    if args.command == "code":
        print(codes.make(Path(Settings().pair_codes_file)))
        return 0
    if args.command == "review":
        print(asyncio.run(Brain().review_now()))
        return 0
    lifeline.watch()
    try:
        asyncio.run(Brain().run())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
