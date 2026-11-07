"""The surroundings as an input: it says nothing, it only keeps the core
told of what is going on around it — what is playing, what is in front,
whether the person is at the desk."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable

from heare.core import Port
from heare.surroundings import sense, sound

logger = logging.getLogger("heare.surroundings")

EVERY_S = 1.5
# After a few rounds that changed nothing, the room is asked less often.
CALM_S = 4.5
CALM_ROUNDS = 3
# Away for five minutes: a locked or sleeping Mac is asked rarely. This is
# the idle clock we already read; the real lock state needs a Mac session.
AWAY_S = 300.0
AWAY_EVERY_S = 15.0
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
        self._idle_now: float | None = None

    async def _safe(self, read: Callable[[], object]) -> object | None:
        try:
            return await asyncio.to_thread(read)
        except Exception as exc:  # noqa: BLE001 — one dead sensor must not blind the others
            logger.warning("a sensor failed: %s", exc)
            return None

    async def _facts(self) -> dict:
        playing = await self._safe(self._playing)
        app = await self._safe(self._front)
        idle = await self._safe(self._idle)
        self._idle_now = float(idle) if isinstance(idle, int | float) else None
        return {
            "playing": playing if isinstance(playing, list) else None,
            "app": app if isinstance(app, str) else None,
            "present": None if idle is None else float(idle) < PRESENT_S,
        }

    async def _note(self, port: Port, channel: str, key: str,
                    value: object) -> bool:
        """Post one fact and say whether the brain took it.

        ``Door.note`` hands back its task; a plain port that returns
        nothing is taken as accepted.
        """
        result = port.note(channel, key, value)
        if not inspect.isawaitable(result):
            return True
        try:
            return bool(await result)
        except Exception as exc:  # noqa: BLE001 — a failed note is retried next round
            logger.warning("could not tell the brain about %s: %s", key, exc)
            return False

    async def run(self, port: Port, gate: Callable[[], bool] | None = None, *,
                  channel: str = "eyes") -> None:
        told: dict = {}
        unknown: set[str] = set()
        silent_for = 0.0
        calm = 0
        while True:
            if gate is not None and not gate():
                await asyncio.sleep(EVERY_S)
                continue
            facts = await self._facts()
            changed = False
            countdown = False
            if facts["playing"]:
                silent_for = 0.0
            elif facts["playing"] is not None:
                silent_for += EVERY_S
                # A gap between two tracks is not the end of the music.
                if told.get("playing") and silent_for < QUIET_AFTER_S:
                    facts["playing"] = told["playing"]
                    countdown = True
            for key, value in facts.items():
                if value is None:
                    if key not in unknown:
                        logger.warning("the %s sensor says unknown", key)
                        unknown.add(key)
                    continue
                if key in unknown:
                    unknown.discard(key)
                if told.get(key, _UNSET) != value:
                    changed = True
                    if await self._note(port, channel, key, value):
                        told[key] = value
            calm = 0 if changed or countdown else calm + 1
            away = self._idle_now is not None and self._idle_now >= AWAY_S
            if away:
                await asyncio.sleep(AWAY_EVERY_S)
            else:
                await asyncio.sleep(EVERY_S if calm < CALM_ROUNDS else CALM_S)
