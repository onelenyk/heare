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

from heare import layout, logs
from heare import lifeline
from heare.core import i18n
from heare.prompt import budget, persona
from heare.tools.ports import Bodies
from heare.api import backup
from heare.api import pair as codes
from heare.config import HOME, Settings
from heare.core import Core
from heare.engine import Engine, Intents
from heare.features import build_features
from heare.language import uk
from heare.memory.contracts import ReviewReport
from heare.memory.review import last_reviewed, mark_reviewed, notice
from heare.mind import Mind

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


def next_review_day(last: date | None, today: date) -> date | None:
    """The day a review owes next, or None when there is nothing due.

    A missing marker means yesterday: the first review is the last whole
    day. A marker older than the catch-up window moves forward a week at a
    time rather than replaying a month, and a marker that already covers
    yesterday means the next review is tomorrow's.
    """
    yesterday = today - timedelta(days=1)
    if last is None:
        return yesterday
    if (yesterday - last).days > REVIEW_CATCH_UP_DAYS:
        last = yesterday - timedelta(days=REVIEW_CATCH_UP_DAYS)
    target = last + timedelta(days=1)
    return None if target > yesterday else target


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
        from heare.model.route import NATIVE as route_native, Router
        from heare.model.judge import Judge
        from heare.tools import Toolbox

        self.settings = settings or Settings()
        names = persona.names()
        self.journal = Journal(self.settings.events_dir)
        self.facts = SQLiteFacts(self.settings.db_path)
        self.intents = Intents(self.settings.intents_path)
        self.memory = Book(
            self.settings.db_path, names=sorted(names),
            profile_dir=self.settings.memory_dir, journal=self.journal,
            facts=self.facts,
        )
        self.mind = Mind(
            self.facts, self.intents, self.settings,
            journal=self.journal, memory=self.memory,
        )
        # What is said to the person follows the language chosen on the page.
        i18n.choose(lambda: str(self.settings.language))
        # The door owns the registry and hands its reader over when it opens.
        self.bodies: Bodies | None = None
        self.core = Core(
            Chat(self.settings),
            memory=self.memory,
            delegator=Router(
                self.settings, lambda: self.core.state.tasks,
                expected=lambda: (Path(self.settings.parts_dir) / route_native / "heare.json").is_file(),
                present=lambda: list(self.core.executors)),
            gate=Judge(self.settings),
            prompt=lambda: persona.prompt(
                self.core.state.around, self.bodies.parts() if self.bodies else None),
            names=names, judge=persona.JUDGE, words=uk,
            fit=partial(budget.fit, limit=int(self.settings.context_tokens)),
        )
        self.core.attach_tools(Toolbox(
            memory=self.core.memory, state=self.core.state,
            delegate=self.core.delegate, stop=self.core.stop_all,
            words=self.core.words,
            has_executors=lambda: bool(self.core.executors),
            reminders=self.intents,
            hands=lambda: [(e.name, e.about) for e in self.core.executors.values()],
            bodies=lambda: self.bodies,
        ))
        # Hands are a body: they register over the door and are added then.
        # The brain itself holds no agent's process.
        self.engine = Engine(
            self.intents, state=self.core.state, speak=self.core.hear,
            quiet=(self.settings.quiet_from, self.settings.quiet_to),
            daily_limit=int(self.settings.unsolicited_per_day),
        )
        self._review_lock = asyncio.Lock()
        self._consolidate_lock = asyncio.Lock()
        self.features = build_features(self)
        self._runner: Any = None

    async def consolidate_once(self) -> ReviewReport:
        """One learning pass, shared by the clock and the page. It reuses
        the consolidation cursor: the old regex pass is no longer run here,
        and that cursor already sits at the end of the journal."""
        async with self._consolidate_lock:
            report = await notice(
                self.facts, self.journal, self.core.model,
                state_path=Path(self.settings.memory_dir) / "consolidate.json",
            )
        if report.error:
            raise RuntimeError(report.error)
        if report.added:
            logger.info("learned %d fact(s)", report.added)
        return report

    async def _consolidate(self) -> None:
        while True:
            await asyncio.sleep(CONSOLIDATE_EVERY_S)
            try:
                await self.consolidate_once()
            except Exception:
                logger.exception("consolidation failed (non-fatal)")

    async def review_once(self) -> ReviewReport | None:
        """Review the day that is due, and mark it only when it succeeded."""
        async with self._review_lock:
            root = Path(self.settings.memory_dir)
            due = next_review_day(last_reviewed(root), date.today())
            if due is None:
                return None
            report = await self.review_now(due)
            if report.error is None:
                mark_reviewed(root, due)
                if report.added:
                    logger.info(
                        "daily review for %s added %d fact(s)",
                        due, report.added,
                    )
            else:
                logger.warning(
                    "daily review for %s failed: %s", due, report.error)
            return report

    async def review_now(self, day: date | None = None) -> ReviewReport:
        from heare.memory.review import review_day

        if day is None:
            day = date.today() - timedelta(days=1)
        return await review_day(
            self.facts, self.journal, self.core.model, day=day
        )

    async def _review(self) -> None:
        """Wait for the hour, then catch up one day at a time.

        Settings are read again every minute, so turning the review off or
        moving its hour does not need a restart. The loop never returns on a
        disabled review: it waits and looks again.
        """
        while True:
            if not bool(self.settings.review_enabled):
                await asyncio.sleep(60.0)
                continue
            now = clock.time()
            hour = int(self.settings.review_hour)
            gate = datetime.combine(date.today(), time(hour=hour)).timestamp()
            if now < gate:
                await asyncio.sleep(min(60.0, gate - now))
                continue
            root = Path(self.settings.memory_dir)
            if next_review_day(last_reviewed(root), date.today()) is None:
                when = next_review_at(now, hour)
                await asyncio.sleep(min(60.0, max(1.0, when - now)))
                continue
            try:
                report = await self.review_once()
            except Exception:
                logger.exception("daily review failed (non-fatal)")
                await asyncio.sleep(REVIEW_RETRY_S)
                continue
            if report is None:
                when = next_review_at(now, hour)
                await asyncio.sleep(min(60.0, max(1.0, when - now)))
                continue
            if report.error:
                await asyncio.sleep(REVIEW_RETRY_S)

    async def run(self) -> None:
        """Live until cancelled: open the door, answer, hold intents."""
        from heare.api.server import serve
        from heare.model import route
        from heare.reach.remote import RemoteExecutor

        self.core.hydrate(self.journal.tail_messages())
        self._runner = await serve(
            self.core, self.settings, lambda: model_info(self.settings),
            make_executor=lambda name, about: route.executor(
                name, about, RemoteExecutor, lambda: list(self.core.executors.values()),
                lambda: self.core.state.tasks),
            mind=self.mind, features=self.features,
            show_bodies=lambda skilled: setattr(self, "bodies", skilled),
        )
        logs.say(f"мозок на http://127.0.0.1:{self.settings.api_port}/")
        await asyncio.gather(
            self.core.run(), self.engine.run(), self.journal.run(self.core.state),
            self._consolidate(), self._review(),
        )


def _unpair(machine: str | None, pid: str | None) -> int:
    if not machine and not pid:
        print("unpair: назви машину, наприклад heare-brain unpair kitchen",
              file=sys.stderr)
        return 1
    from heare.api import auth

    tokens = Path(Settings().part_tokens_file)
    status = 0
    if machine:
        target = f"machine@{machine}"
        if auth.revoke(tokens, target):
            print(f"revoked {target}")
        else:
            print(f"no such machine: {machine}", file=sys.stderr)
            status = 1
    if pid:
        if auth.revoke(tokens, pid):
            print(f"revoked {pid}")
        else:
            print(f"no such part: {pid}", file=sys.stderr)
            status = 1
    return status


def main() -> int:
    parser = argparse.ArgumentParser(prog="heare-brain")
    parser.add_argument(
        "command", nargs="?", default="run",
        choices=["run", "code", "review", "backup", "restore", "unpair"],
    )
    parser.add_argument(
        "file", nargs="?",
        help="restore: the backup to put back; unpair: the machine to forget",
    )
    parser.add_argument("--no-sound", action="store_true",
                        help="backup: without the recordings")
    parser.add_argument("--pid",
                        help="unpair: also take this part's key away")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logs.setup(args.verbose)
    layout.settle()
    if args.command == "unpair":
        return _unpair(args.file, args.pid)
    if args.command == "backup":
        from heare.api.server import running
        print(backup.make(HOME, sound=not args.no_sound, version=running()["version"]))
        return 0
    if args.command == "restore":
        if not args.file:
            parser.error("restore: назви файл копії")
        try:
            made = backup.stage(HOME, Path(args.file)).get("made")
        except backup.BackupError as exc:
            print(f"не відновлю: {exc}", file=sys.stderr)
            return 1
        print(f"копію від {made} підготовлено; вона стане на місце, коли мозок "
              "запуститься наступного разу — перезапусти heare")
        return 0
    if args.command == "code":
        print(codes.make(Path(Settings().pair_codes_file)))
        return 0
    if args.command == "review":
        print(asyncio.run(Brain().review_now()))
        return 0
    lifeline.watch()
    if (done := backup.apply(HOME)) is not None:
        logs.say(f"відновлено з копії {done['name']}; що було — у backups/{done['aside']}")
    try:
        asyncio.run(Brain().run())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
