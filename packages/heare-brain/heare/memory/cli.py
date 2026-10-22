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
    return parser


def _text(args: argparse.Namespace) -> str:
    if args.text is not None:
        return args.text
    return sys.stdin.read()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
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
