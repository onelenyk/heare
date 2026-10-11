"""The listener, pointed at this app.

    uv run python -m heare.listener [rounds]

Real speech into the voice channel, the real recogniser, model and
synthesiser, and a second recogniser on what would have left the
speaker. It costs cents and needs the keys, so it is run on purpose and
is not part of the test suite.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from heare.app import build
from heare.channels.voice.channel import build as build_voice
from heare.config import Settings
from heare.listener import scenarios
from heare.listener.tape import Tape


async def main() -> int:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    settings = Settings()
    tape = Tape()
    await tape.start()
    core = build(settings)
    voice = build_voice(tape, settings)
    core.add_input(voice)
    core.add_output(voice)
    running = asyncio.create_task(core.run())
    await asyncio.sleep(0.5)
    try:
        summary = await scenarios.run(tape, rounds)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await tape.stop()
    return 1 if summary["answers_with_problems"] else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    sys.exit(asyncio.run(main()))
