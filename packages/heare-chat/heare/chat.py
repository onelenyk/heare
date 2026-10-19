"""A text body: type to the brain, read what it says back.

    uv run python -m heare.chat

A body, not the brain: it holds nothing and remembers nothing. It sends
what you type to the brain's door and prints what comes back. Run the brain
first — ``uv run python -m heare.brain``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import sys

from heare import part

UI = [
    {"name": "printing", "kind": "toggle", "label": "Друкувати", "value": True},
    {"name": "state", "kind": "label", "label": "Стан", "head": True,
     "value": "слухає термінал"},
]


def _show(event: dict, name: str) -> None:
    """Print what the brain said; note the things it could not do."""
    if event.get("kind") == "said" and event.get("text"):
        print(f"{name}: {event['text']}", flush=True)
    elif event.get("kind") in ("failed", "cannot"):
        print(f"  ({event.get('why') or event['kind']})", flush=True)


class Printer:
    """The brain's events, printed while the person wants them."""

    def __init__(self) -> None:
        self.name = "heare"
        self.values = {"printing": True, "state": "слухає термінал"}
        self.wire: part.Wire | None = None

    def event(self, event: dict) -> None:
        if event.get("kind") == "control" and event.get("channel") == "text":
            if event.get("name") == "printing":
                self.values["printing"] = bool(event.get("value"))
                self.values["state"] = ("слухає термінал" if self.values["printing"]
                                        else "мовчить")
                if self.wire is not None:
                    self.wire.report(self.values)
            return
        if self.values["printing"]:
            _show(event, self.name)


def _line() -> str | None:
    """One line from the terminal, read without readline's thread trouble.

    ``input`` goes through readline, which is not safe from a thread while
    another task writes the reply to the same terminal: it has read one
    multi-byte character in pieces and raised UnicodeDecodeError. ``sys.stdin``
    is asked directly instead, and a mangled byte becomes «�», not a crash.
    """
    print("ти: ", end="", flush=True)
    line = sys.stdin.readline()
    return line.rstrip("\n") if line else None


async def _typed(wire: part.Wire) -> None:
    """Send each line you type to the brain, until the input ends."""
    while True:
        line = await asyncio.to_thread(_line)
        if line is None:
            return
        if line.strip():
            await wire.post("/api/input", {
                "channel": "text", "kind": "heard", "text": line.strip(),
                "addressed": True,
            })


async def run() -> int:
    try:
        sys.stdin.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass
    host = socket.gethostname()
    printer = Printer()

    async def work(wire: part.Wire) -> None:
        printer.wire, printer.name = wire, wire.name or printer.name
        wire.report(printer.values)
        await _typed(wire)

    # The chat shows the whole conversation, not only what was typed here.
    return await part.live(
        {"channel": "text", "forms": ["input", "output"],
         "about": "чат у терміналі", "key": f"text@{host}", "host": host,
         "ui": UI, "hears": "all"},
        work, on_event=printer.event)


def main() -> int:
    parser = argparse.ArgumentParser(prog="heare.chat")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
