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
from pathlib import Path

import httpx

from heare.config import Settings
from heare.core import Message
from heare.surroundings.watch import Surroundings

logger = logging.getLogger("heare.eyes")

ABOUT = "очі: застосунок, простій, чи щось грає"


def _token(settings: Settings) -> str:
    return Path(settings.api_token_file).read_text(encoding="utf-8").strip()


class Door:
    """The brain's door as the surroundings see it.

    ``Surroundings`` only calls ``note``; the rest are here so the object
    is a port, and do nothing.
    """

    name = "eyes"

    def __init__(self, client: httpx.AsyncClient, base: str) -> None:
        self._client, self._base = client, base
        self._sending: set[asyncio.Task] = set()

    def note(self, key: str, value: object) -> None:
        """Post the fact without waiting: a sensor must not stall the loop."""
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
    base = f"http://127.0.0.1:{settings.api_port}"
    try:
        secret = _token(settings)
    except OSError:
        print(f"немає ключа ({settings.api_token_file}) — мозок запущений?",
              file=sys.stderr)
        return 1
    headers = {"X-Heare-Token": secret}
    async with httpx.AsyncClient(headers=headers, timeout=None) as client:
        host = socket.gethostname()
        print("стучу в мозок… (нова частина чекає на одобрення на сторінці)",
              file=sys.stderr)
        try:
            answer = (await client.post(f"{base}/api/bodies", json={
                "channel": "eyes", "forms": ["input"], "about": ABOUT,
                "key": f"eyes@{host}", "host": host,
            })).json()
        except httpx.HTTPError as exc:
            print(f"мозок не відповідає на {base}: {exc}", file=sys.stderr)
            return 1
        if not answer.get("approved"):
            print(f"мозок не одобрив: {answer.get('why') or 'відхилено'}",
                  file=sys.stderr)
            return 1
        print("очі: дивлюсь навколо", file=sys.stderr)
        door = Door(client, base)
        try:
            await Surroundings().run(door)
        finally:
            await door.settle()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="heare.eyes")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
