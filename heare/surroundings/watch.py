"""The surroundings as an input: it says nothing, it only keeps the core
told of what is going on around it."""

from __future__ import annotations

import asyncio

from heare.core import Port
from heare.surroundings import sound

EVERY_S = 1.5
# Sound that stops for a moment between two tracks has not stopped.
QUIET_AFTER_S = 4.0


class Surroundings:
    name = "surroundings"

    def __init__(self, sense=sound.playing) -> None:
        self._sense = sense

    async def run(self, port: Port) -> None:
        told: list[str] | None = None
        silent_for = 0.0
        while True:
            now = await asyncio.to_thread(self._sense)
            if now:
                silent_for = 0.0
            else:
                silent_for += EVERY_S
                if told and silent_for < QUIET_AFTER_S:
                    now = told  # not yet: it may be a gap
            if now != told:
                told = now
                port.note("playing", now)
            await asyncio.sleep(EVERY_S)
