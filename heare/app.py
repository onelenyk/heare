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
from heare.language import uk
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

    dispatcher = Dispatcher(settings)
    core = Core(
        Chat(settings), memory=Book(settings.db_path, names=sorted(names)),
        delegator=dispatcher, prompt=persona.prompt, names=names,
        judge=persona.JUDGE, words=uk,
    )
    # Whichever agents this machine has. None is fine: the verb is then
    # simply not offered, and the assistant only talks and remembers.
    for agent in connect(settings):
        core.add_executor(agent)
    return core


def attach_surroundings(core: Core) -> None:
    """Keep the core told of what is going on around it."""
    from heare.surroundings.watch import Surroundings

    core.add_input(Surroundings())


def _model(settings: Settings) -> dict:
    """Which model is answering — and whether it is the one that was
    chosen. A chosen provider with no key falls back to another, and that
    must be visible: the old dashboard showed a choice that changed nothing."""
    from heare.model.llm import resolve_llm
    from heare.model.providers import PROVIDERS

    wanted = str(settings.llm_provider or "deepseek").lower()
    logging.getLogger("heare.spine.llm").disabled = True  # asked often; said once elsewhere
    try:
        cfg = resolve_llm(settings)
    except RuntimeError as exc:
        return {"wanted": wanted, "error": str(exc), "name": persona.name()}
    finally:
        logging.getLogger("heare.spine.llm").disabled = False
    return {
        "provider": cfg.provider, "model": cfg.model, "wanted": wanted,
        "fallback": cfg.provider != wanted, "providers": sorted(PROVIDERS),
        "name": persona.name(),
    }


async def _main(args: argparse.Namespace) -> int:
    settings = Settings()
    core = build(settings)
    ended: list[asyncio.Event] = []
    if args.serve:
        from heare.api.server import serve

        await serve(core, settings, lambda: _model(settings))
        print(f"екран: http://127.0.0.1:{settings.api_port}/", file=sys.stderr)
    if args.voice:
        from heare.channels.voice.channel import open_voice

        core_voice = await open_voice(settings)
        core.add_input(core_voice)
        core.add_output(core_voice)
        attach_surroundings(core)
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
