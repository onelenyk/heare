"""Assembly: the one file that knows every part.

    uv run python -m heare            # talk by typing
    uv run python -m heare --voice    # talk aloud
    uv run python -m heare --serve    # the screen and the API (add --voice for both)

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
    """The core with a model, a memory and a character; no channel yet."""
    from heare.model.chat import Chat

    from heare.memory.book import Book

    settings = settings or Settings()
    names = persona.names()
    from heare.model.delegate import Dispatcher
    from heare.reach.agents import connect

    from heare.api.screens import ScreenMaker

    dispatcher = Dispatcher(settings)
    core = Core(
        Chat(settings), memory=Book(settings.db_path, names=sorted(names)),
        delegator=dispatcher, prompt=persona.prompt, names=names,
        judge=persona.JUDGE,
    )
    core.add_executor(
        ScreenMaker(settings.screens_dir, dispatcher.ask, core.state.emit)
    )
    # Whichever agents this machine has. None is fine: the verb is then
    # simply not offered, and the assistant only talks and remembers.
    for agent in connect(settings):
        core.add_executor(agent)
    return core


async def _main(args: argparse.Namespace) -> int:
    settings = Settings()
    core = build(settings)
    ended: list[asyncio.Event] = []
    if args.serve:
        from heare.api.server import serve

        await serve(core, settings)
        print(f"екран: http://127.0.0.1:{settings.api_port}/", file=sys.stderr)
    if args.voice:
        from heare.channels.voice.channel import open_voice

        core_voice = await open_voice(settings)
        core.add_input(core_voice)
        core.add_output(core_voice)
        print("слухаю (Ctrl+C — вихід)", file=sys.stderr)
    elif not args.serve:
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
    parser.add_argument("--serve", action="store_true", help="the API and the screen")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    try:
        return asyncio.run(_main(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
