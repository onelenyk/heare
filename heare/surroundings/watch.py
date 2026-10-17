"""The surroundings as an input: it says nothing, it only keeps the core
told of what is going on around it — what is playing, what is in front,
whether the person is at the desk."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from heare.core import Port
from heare.surroundings import sense, sound

EVERY_S = 1.5
# Sound that stops for a moment between two tracks has not stopped.
QUIET_AFTER_S = 4.0
# At the keyboard within the last minute: the person is present.
PRESENT_S = 60.0

_UNSET = object()


class Surroundings:
    name = "surroundings"

    def __init__(self, playing=sound.playing, front=sense.front_app,
                 idle=sense.idle_seconds) -> None:
        self._playing = playing
        self._front = front
        self._idle = idle

    async def _facts(self) -> dict:
        return {
            "playing": await asyncio.to_thread(self._playing),
            "app": await asyncio.to_thread(self._front),
            "present": (await asyncio.to_thread(self._idle)) < PRESENT_S,
        }

    async def run(self, port: Port, gate: Callable[[], bool] | None = None) -> None:
        told: dict = {}
        silent_for = 0.0
        while True:
            if gate is not None and not gate():
                await asyncio.sleep(EVERY_S)
                continue
            facts = await self._facts()
            if facts["playing"]:
                silent_for = 0.0
            else:
                silent_for += EVERY_S
                # A gap between two tracks is not the end of the music.
                if told.get("playing") and silent_for < QUIET_AFTER_S:
                    facts["playing"] = told["playing"]
            for key, value in facts.items():
                if told.get(key, _UNSET) != value:
                    told[key] = value
                    port.note(key, value)
            await asyncio.sleep(EVERY_S)
