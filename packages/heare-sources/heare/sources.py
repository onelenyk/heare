"""The sources body: what is true now, posted to the brain's door.

    uv run python -m heare.sources

A body, not the brain: it fetches a fact on a timer and posts it as a note
that says until when it stays true, so the brain never has to guess. Stop
it and the brain keeps running; the last note simply stops being true.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import socket
import sys
import time
from pathlib import Path

import httpx

from heare import part
from heare.config import HOME, Settings
from heare.feeds import weather

logger = logging.getLogger("heare.sources")

CHANNEL = "sources"
ABOUT = "джерела: погода — що зараз надворі"
HELLO = "джерела: дивлюсь, що надворі"
ROUND_S = 30 * 60
# A fact from Open-Meteo is true for this long after it was fetched.
TRUE_FOR_S = 90 * 60
WEATHER_TIMEOUT_S = 15.0
SETTINGS_FILE = HOME / "sources.json"
DEFAULTS = {"weather": True, "city": "Київ"}
UI = [
    {"name": "weather", "kind": "toggle", "label": "Погода", "value": True},
    {"name": "city", "kind": "text", "label": "Місто", "value": DEFAULTS["city"]},
    {"name": "sees", "kind": "label", "label": "Зараз", "value": "—", "head": True},
]


def load(path: Path) -> dict:
    """What the person chose last time; the defaults for anything missing."""
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULTS)
    if not isinstance(saved, dict):
        return dict(DEFAULTS)
    return {**DEFAULTS, **{key: saved[key] for key in DEFAULTS if key in saved}}


def save(path: Path, chosen: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(chosen, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning("could not keep the sources settings: %s", exc)


class Sources:
    """The part's state: what the card shows and what the loop fetches."""

    def __init__(self, web: httpx.AsyncClient, path: Path) -> None:
        self._web, self._path = web, path
        saved = load(path)
        self.weather = bool(saved["weather"])
        self.city = str(saved["city"])
        self.sees = "—"
        # Once the part is let in, reports and notes go through its wire.
        self.wire: part.Wire | None = None
        # Set when the person changes a setting, so the next fetch is now
        # and not in half an hour.
        self.wake = asyncio.Event()

    def values(self) -> dict:
        return {"weather": self.weather, "city": self.city, "sees": self.sees}

    def event(self, event: dict) -> None:
        if event.get("kind") != "control":
            return
        name = event.get("name")
        if name == "weather":
            self.weather = bool(event.get("value"))
            if not self.weather:
                self.sees = "вимкнено"
        elif name == "city":
            city = str(event.get("value") or "").strip()
            if not city:
                return
            self.city = city
        else:
            return
        save(self._path, {"weather": self.weather, "city": self.city})
        self.tell()
        self.wake.set()

    def tell(self) -> None:
        if self.wire is not None:
            self.wire.report(self.values())

    def show(self, text: str) -> None:
        self.sees = text
        self.tell()

    async def look(self) -> None:
        """One round: fetch what is true now and post it, if the person wants it.

        A failed fetch is logged and shown; the next round tries again. It
        never ends the part.
        """
        if not self.weather or self.wire is None:
            return
        try:
            found = await weather.fetch(self._web, self.city)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            logger.warning("could not fetch the weather for %s: %s", self.city, exc)
            self.show("немає зв'язку з погодою, спробую ще")
            return
        if found is None:
            self.show("місто не знайдено")
            return
        label, text = found
        self.show(text)
        await self.post(
            "weather", {"text": text, "until": time.time() + TRUE_FOR_S, "label": label}
        )

    async def post(self, key: str, value: dict) -> None:
        assert self.wire is not None
        answer = await self.wire.post(
            "/api/input",
            {"channel": CHANNEL, "kind": "note", "key": key, "value": value},
        )
        try:
            answer.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("could not tell the brain about %s: %s", key, exc)


async def run() -> int:
    settings = Settings()
    host = socket.gethostname()
    async with (
        httpx.AsyncClient(timeout=None) as client,
        httpx.AsyncClient(timeout=WEATHER_TIMEOUT_S) as web,
    ):
        sources = Sources(web, SETTINGS_FILE)

        async def work(wire: part.Wire) -> None:
            sources.wire = wire
            sources.tell()
            while True:
                sources.wake.clear()
                await sources.look()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(sources.wake.wait(), ROUND_S)

        return await part.live(
            {
                "channel": CHANNEL,
                "forms": ["input"],
                "about": ABOUT,
                "key": f"sources@{host}",
                "host": host,
                "ui": UI,
            },
            work,
            on_event=sources.event,
            hello=HELLO,
            settings=settings,
            client=client,
        )


def main() -> int:
    parser = argparse.ArgumentParser(prog="heare.sources")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
