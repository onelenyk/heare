"""Assembly: the one file that knows every part.

    uv run python -m heare            # talk by typing
    uv run python -m heare --voice    # talk aloud

Everything else knows the core and itself. If a second file starts
needing to know two parts, the shape has gone wrong.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from heare import persona
from heare.config import Settings
from heare.core import Core


def build(settings: Settings | None = None) -> Core:
    """The core with a model and a character, and nothing attached yet."""
    from heare.model.chat import Chat

    settings = settings or Settings()
    return Core(Chat(settings), prompt=persona.prompt, names=persona.names())


async def _main(args: argparse.Namespace) -> int:
    core = build()
    ended: list[asyncio.Event] = []
    if args.voice:
        from heare.channels.voice.channel import open_voice

        core_voice = await open_voice(Settings())
        core.add_input(core_voice)
        core.add_output(core_voice)
        print("слухаю (Ctrl+C — вихід)", file=sys.stderr)
    else:
        from heare.channels.text.terminal import Terminal

        terminal = Terminal()
        core.add_input(terminal)
        core.add_output(terminal)
        ended.append(terminal.closed)
    running = asyncio.create_task(core.run())
    try:
        if ended:
            await ended[0].wait()
            # The pipe ended; let what was asked be answered before leaving.
            while not core.settled:
                await asyncio.sleep(0.05)
        else:
            await running
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="heare")
    parser.add_argument("--voice", action="store_true", help="microphone and speaker")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    try:
        return asyncio.run(_main(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
