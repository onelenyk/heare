"""The eyes body: what is going on around the Mac, told to the brain.

    uv run python -m heare.eyes

A body, not the brain: it holds no memory and decides nothing. It reads
the front app, idleness, and who is making sound, and posts each change to
the brain's door. Stop it and the brain keeps running.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import sys

import httpx

from heare import layout, logs
from heare import part
from heare.config import Settings
from heare.core import Message
from heare.surroundings.watch import Surroundings

logger = logging.getLogger("heare.eyes")

ABOUT = "eyes: which app is open on the Mac (its name only), whether something plays"
UI = [
    {"name": "looking", "kind": "toggle", "label": "Дивитись", "value": True},
    {"name": "sees", "kind": "label", "label": "Бачить", "value": "тихо",
     "head": True},
]


class Door:
    """The brain's door as the surroundings see it.

    ``Surroundings`` only calls ``note``; the rest are here so the object
    is a port, and do nothing.
    """

    name = "eyes"

    def __init__(self, client: httpx.AsyncClient, base: str) -> None:
        self._client, self._base = client, base
        self._sending: set[asyncio.Task] = set()
        # Once the part is let in, reports go through its wire, which keeps
        # them to tell a brain that restarted.
        self.wire: part.Wire | None = None
        self.looking = True
        self.sees = "тихо"

    def values(self) -> dict:
        return {"looking": self.looking, "sees": self.sees}

    def event(self, event: dict) -> None:
        if event.get("kind") == "control" and event.get("name") == "looking":
            self.looking = bool(event.get("value"))
            self.tell()

    def tell(self) -> None:
        if self.wire is not None:
            self.wire.report(self.values())
            return
        task = asyncio.create_task(
            part.report(self._client, self._base, self.name, self.values()))
        self._sending.add(task)
        task.add_done_callback(self._sending.discard)

    def note(self, key: str, value: object) -> None:
        """Post the fact without waiting: a sensor must not stall the loop."""
        if not self.looking:
            return
        if key == "app":
            self.sees = str(value or "тихо")
        task = asyncio.create_task(self._post(key, value))
        self._sending.add(task)
        task.add_done_callback(self._sending.discard)

    async def _post(self, key: str, value: object) -> None:
        try:
            answer = await self._client.post(f"{self._base}/api/input", json={
                "channel": self.name, "kind": "note", "key": key, "value": value,
            })
            answer.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("could not tell the brain about %s: %s", key, exc)
        if key == "app":
            self.tell()

    async def settle(self) -> None:
        """Wait for every note already posted to reach the door."""
        while self._sending:
            await asyncio.gather(*self._sending, return_exceptions=True)

    def started(self, channel: str) -> None: ...

    def heard(self, message: Message) -> None: ...

    def nothing(self, channel: str) -> None: ...

    def overheard(self, channel: str, text: str) -> None: ...

    def said_over(self, channel: str, text: str, speech_ms: float,
                  long: bool) -> bool:
        return False


async def run() -> int:
    settings = Settings()
    host = socket.gethostname()
    async with httpx.AsyncClient(timeout=None) as client:
        door = Door(client, settings.base)

        async def work(wire: part.Wire) -> None:
            door.wire = wire
            door.tell()
            try:
                await Surroundings().run(door, gate=lambda: door.looking)
            finally:
                await door.settle()

        return await part.live(
            {"channel": "eyes", "forms": ["input"], "about": ABOUT,
             "key": f"eyes@{host}", "host": host, "ui": UI},
            work, on_event=door.event, hello="очі: дивлюсь навколо",
            settings=settings, client=client)


def main() -> int:
    parser = argparse.ArgumentParser(prog="heare.eyes")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logs.setup(args.verbose)
    layout.settle()
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
