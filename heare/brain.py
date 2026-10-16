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
from typing import Any

from heare import persona
from heare.config import Settings
from heare.core import Core
from heare.engine import Engine, Intents
from heare.language import uk


def model_info(settings: Settings) -> dict:
    """Which model is answering — and whether it is the one that was chosen.

    A chosen provider with no key falls back to another, and that has to be
    visible: the old dashboard showed a choice that changed nothing.
    """
    from heare.model.llm import resolve_llm
    from heare.model.providers import PROVIDERS

    wanted = str(settings.llm_provider or "deepseek").lower()
    logging.getLogger("heare.spine.llm").disabled = True
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


class Brain:
    """Everything that thinks, and runs on its own."""

    def __init__(self, settings: Settings | None = None) -> None:
        from heare.memory.book import Book
        from heare.model.chat import Chat
        from heare.model.delegate import Dispatcher
        from heare.reach.agents import connect

        self.settings = settings or Settings()
        names = persona.names()
        self.core = Core(
            Chat(self.settings),
            memory=Book(self.settings.db_path, names=sorted(names)),
            delegator=Dispatcher(self.settings), prompt=persona.prompt,
            names=names, judge=persona.JUDGE, words=uk,
        )
        # Whichever agents this machine has. None is fine: the verb is then
        # simply not offered, and the brain only talks and remembers.
        for agent in connect(self.settings):
            self.core.add_executor(agent)
        self.engine = Engine(Intents(self.settings.intents_path),
                             state=self.core.state, speak=self.core.hear)
        self._runner: Any = None

    async def run(self) -> None:
        """Live until cancelled: open the door, answer, hold intents."""
        from heare.api.server import serve

        self._runner = await serve(
            self.core, self.settings, lambda: model_info(self.settings)
        )
        print(f"мозок на http://127.0.0.1:{self.settings.api_port}/", file=sys.stderr)
        await asyncio.gather(
            self.core.run(), self.engine.run()
        )


def main() -> int:
    parser = argparse.ArgumentParser(prog="heare.brain")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    try:
        asyncio.run(Brain().run())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
