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
import sys
from pathlib import Path

from heare.memory.journal import Journal
from heare.memory.migrate import migrate
from heare.memory.profile import FILES, Profile

EMPTY = "(порожній профіль)"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="heare-memory")
    parser.add_argument(
        "--root", type=Path, default=Path.home() / ".heare" / "memory",
        help="where the profile blocks live",
    )
    parser.add_argument(
        "--events", type=Path, default=Path.home() / ".heare" / "events",
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
        "--db", type=Path, default=Path.home() / ".heare" / "heare.db",
        help="the fact store to fill",
    )
    migration.add_argument("--include-archived", action="store_true")
    migration.add_argument("--dry-run", action="store_true")
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
    profile = Profile(args.root)
    if args.command == "show":
        print(profile.render() or EMPTY)
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
