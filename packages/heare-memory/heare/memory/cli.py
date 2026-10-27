"""The owner's hand on the profile: read it, replace a block, add to one.

    heare-memory show
    heare-memory set user --text "Мене звати Назар."
    heare-memory add goals --text "Запустити heare."

This is the only new writer of the curated files: the model still cannot
write them. Every successful edit leaves a ``profile_edited`` event, so
the journal says when a fact changed even though the files themselves are
the projection.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from heare.memory.consolidate import consolidate
from heare.memory.journal import Journal
from heare.memory.migrate import migrate
from heare.memory.profile import FILES, Profile
from heare.core.i18n import N_, _

_MEMORY = Path.home() / ".heare" / "memory"

EMPTY = N_("(empty profile)")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="heare-memory")
    parser.add_argument(
        "--root", type=Path, default=_MEMORY / "profile",
        help="where the profile blocks live",
    )
    parser.add_argument(
        "--events", type=Path, default=_MEMORY / "journal",
        help="where the journal writes profile_edited",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show", help="print the rendered profile")
    for name in ("set", "add"):
        command = commands.add_parser(name, help=f"{name} a block")
        command.add_argument("key")
        command.add_argument("--text", help="the block's text (default: stdin)")
    clear = commands.add_parser("clear", help="empty a block")
    clear.add_argument("key")
    migration = commands.add_parser("migrate", help="import the old memories table")
    migration.add_argument(
        "--from", dest="legacy", required=True, type=Path,
        help="the old heare.db holding memories",
    )
    migration.add_argument(
        "--db", type=Path, default=_MEMORY / "heare.db",
        help="the fact store to fill",
    )
    migration.add_argument("--include-archived", action="store_true")
    migration.add_argument("--dry-run", action="store_true")

    facts = commands.add_parser("facts", help="list proposed facts")
    facts.add_argument(
        "--db", type=Path, default=_MEMORY / "heare.db",
        help="the fact store to read",
    )
    facts.add_argument("--all", action="store_true", help="show every status")
    for name, help_text in (
        ("confirm", "confirm a proposed fact"),
        ("reject", "reject a proposed fact"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("id")
        command.add_argument(
            "--db", type=Path, default=_MEMORY / "heare.db",
            help="the fact store to change",
        )
    consolidation = commands.add_parser(
        "consolidate", help="propose facts from the journal"
    )
    consolidation.add_argument(
        "--db", type=Path, default=_MEMORY / "heare.db",
        help="the fact store to fill",
    )
    consolidation.add_argument(
        "--events", type=Path, default=argparse.SUPPRESS,
        help="where the journal lives (default: the global --events)",
    )
    forget = commands.add_parser(
        "forget", help="forget events in the journal and in recall"
    )
    forget.add_argument("ids", nargs="+", metavar="ID")
    forget.add_argument(
        "--db", type=Path, default=_MEMORY / "heare.db",
        help="the transcript store to clean",
    )
    forget.add_argument(
        "--events", type=Path, default=argparse.SUPPRESS,
        help="where the journal lives (default: the global --events)",
    )
    return parser


def _text(args: argparse.Namespace) -> str:
    if args.text is not None:
        return args.text
    return sys.stdin.read()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "migrate":
        from heare.memory.facts import SQLiteFacts

        report = migrate(
            args.legacy,
            SQLiteFacts(args.db),
            include_archived=args.include_archived,
            dry_run=args.dry_run,
        )
        print(report)
        return 0
    if args.command in ("facts", "confirm", "reject"):
        from heare.memory.contracts import PENDING
        from heare.memory.facts import SQLiteFacts

        store = SQLiteFacts(args.db)
        if args.command == "facts":
            for fact in store.current(include_pending=True):
                if args.all or fact.status == PENDING:
                    print(f"{fact.id}. [{fact.status}] {fact.text}")
            return 0
        try:
            fact_id = int(args.id)
        except ValueError:
            print(_("Say which fact."))
            return 2
        if args.command == "confirm":
            found = store.confirm(fact_id)
            print(_("Confirmed.") if found else _("Not found."))
        else:
            found = store.reject(fact_id)
            print(_("Rejected.") if found else _("Not found."))
        return 0 if found else 2
    if args.command == "consolidate":
        from heare.memory.facts import SQLiteFacts

        report = consolidate(SQLiteFacts(args.db), Journal(args.events))
        print(report)
        return 0
    if args.command == "forget":
        from heare.memory.book import Book

        book = Book(args.db, journal=Journal(args.events))
        try:
            forgotten = asyncio.run(book.forget(args.ids))
        finally:
            book.close()
        print(_("Forgot {n}.").format(n=forgotten))
        return 0
    profile = Profile(args.root)
    if args.command == "show":
        print(profile.render() or _(EMPTY))
        return 0
    key = args.key
    if key not in FILES:
        print(f"not a profile block: {key}", file=sys.stderr)
        return 2
    append = args.command == "add"
    text = "" if args.command == "clear" else _text(args)
    profile.write(key, text, append=append)
    Journal(args.events).append(
        "profile_edited",
        {"key": key, "chars": len(text), "append": append},
        source="cli",
        trust="user_confirmed",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
