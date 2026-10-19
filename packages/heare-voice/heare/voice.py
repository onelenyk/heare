"""The voice body: the microphone and the speaker, connected over the door.

    uv run python -m heare.voice

A body, not the brain: it holds nothing and decides nothing. It hears,
sends what it heard to the brain's door, and speaks what comes back.
Run the brain first — ``uv run python -m heare.brain``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import socket
import sys
from collections.abc import AsyncIterator

import httpx

from heare import part
from heare.channels.voice.channel import Voice, open_voice
from heare.config import Settings
from heare.core import Message

logger = logging.getLogger("heare.voice.body")

CHANNEL = "voice"
_END = object()

# What the person can touch on this body. The values here are the defaults
# the page shows until the voice is open and reports the real ones.
UI = [
    {"name": "mic", "kind": "toggle", "label": "Мікрофон", "value": True},
    {"name": "speaker", "kind": "toggle", "label": "Звук", "value": True},
    {"name": "volume", "kind": "slider", "label": "Гучність", "value": 1.0,
     "min": 0, "max": 1, "step": 0.05},
    {"name": "listen", "kind": "choice", "label": "Відповідає", "value": "judge",
     "options": [{"value": "judge", "label": "сам вирішує"},
                 {"value": "name", "label": "лише на ім'я"}]},
    {"name": "mode", "kind": "label", "label": "Стан", "value": "чує",
     "head": True},
]


def values_of(voice: Voice) -> dict:
    """The controls' present worth, with the one line a person reads first."""
    values = dict(voice.controls)
    values["mode"] = "чує" if values.get("mic") else "на паузі"
    return values


class HttpPort:
    """The core's port, spoken over the door.

    Every method is synchronous because the voice calls them from inside
    its own loop: a dropped report is noted and forgotten, never awaited.
    """

    def __init__(self, client: httpx.Client, base: str,
                 wire: part.Wire | None = None) -> None:
        self._client, self._base = client, base
        # The part's wire keeps the last values, to tell a brain that restarted.
        self._wire = wire

    def _post(self, path: str, payload: dict) -> httpx.Response | None:
        try:
            response = self._client.post(f"{self._base}{path}", json=payload)
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            logger.warning("the brain did not take %s: %s", path, exc)
            return None

    def started(self, channel: str) -> None:
        self._post("/api/input", {"channel": channel, "kind": "started"})

    def heard(self, message: Message) -> None:
        payload = {
            "channel": message.channel, "kind": "heard", "text": message.text,
            "addressed": message.addressed,
        }
        if message.speech_ms is not None:
            payload["speech_ms"] = message.speech_ms
        self._post("/api/input", payload)

    def nothing(self, channel: str) -> None:
        self._post("/api/input", {"channel": channel, "kind": "nothing"})

    def note(self, key: str, value: object) -> None:
        self._post("/api/input", {
            "channel": CHANNEL, "kind": "note", "key": key, "value": value,
        })

    def overheard(self, channel: str, text: str) -> None:
        self._post("/api/input", {
            "channel": channel, "kind": "overheard", "text": text,
        })

    def state(self, values: dict) -> None:
        if self._wire is not None:
            self._wire.values.update(values)
        self._post("/api/input", {
            "channel": CHANNEL, "kind": "state", "values": dict(values),
        })

    def sounding(self, on: bool) -> None:
        """Whether sound is still leaving the body — the brain waits on it."""
        self._post("/api/input", {
            "channel": CHANNEL, "kind": "state",
            "values": {"speaking": bool(on)},
        })

    def said_over(self, channel: str, text: str, speech_ms: float,
                  long: bool) -> bool:
        response = self._post("/api/input/said_over", {
            "channel": channel, "text": text, "speech_ms": speech_ms,
            "long": long,
        })
        return bool(response.json()["turn"]) if response is not None else False


class _Reply:
    """One reply's pieces, queued until the mouth is ready for them."""

    def __init__(self, voice: Voice, port: HttpPort | None = None) -> None:
        self._voice = voice
        self._port = port
        self._queue: asyncio.Queue[str | object] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def piece(self, text: str) -> None:
        self._queue.put_nowait(text)
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    def end(self) -> None:
        self._queue.put_nowait(_END)

    async def _pieces(self) -> AsyncIterator[str]:
        while True:
            piece = await self._queue.get()
            if piece is _END:
                return
            yield piece

    async def _run(self) -> None:
        if self._port is not None:
            self._port.sounding(True)
        try:
            logger.info("the reply %s", await self._voice.say(self._pieces()))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("the mouth failed mid-reply")
        finally:
            if self._port is not None:
                self._port.sounding(False)

    async def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)


class _Speaker:
    """What the brain streams, applied to one voice body."""

    def __init__(self, voice: Voice, port: HttpPort | None = None) -> None:
        self._voice = voice
        self._port = port
        self._reply: _Reply | None = None
        self._sounded = False

    def feed(self, event: dict) -> None:
        if event.get("channel") != CHANNEL:
            return
        kind = event.get("kind")
        if kind == "reply":
            self._piece(str(event.get("text", "")))
        elif kind == "said":
            self._end()
        elif kind == "control":
            self._turn(str(event.get("name", "")), event.get("value"))
        elif kind == "voice_paused":
            self._voice.pause()
        elif kind == "voice_resumed":
            self._voice.resume()
        elif kind == "voice_stopped":
            self._voice.stop()
            self._end()

    def _piece(self, text: str) -> None:
        if self._reply is None:
            if self._sounded:
                # The last reply's text ended, its sound may not have: a new
                # one begins on a clean line.
                self._voice.stop()
                self._sounded = False
            self._reply = _Reply(self._voice, self._port)
        self._reply.piece(text)

    def _end(self) -> None:
        if self._reply is not None:
            self._reply.end()
            self._reply = None
            self._sounded = True

    def _turn(self, name: str, value) -> None:
        try:
            self._voice.set_control(name, value)
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("the voice refused %r: %s", name, exc)
        if self._port is not None:
            self._port.state(values_of(self._voice))

    async def cancel(self) -> None:
        reply, self._reply = self._reply, None
        if reply is not None:
            await reply.cancel()


async def _follow(lines: AsyncIterator[str], speaker: _Speaker) -> None:
    async for line in lines:
        if not line.startswith("data: "):
            continue
        try:
            speaker.feed(json.loads(line[6:]))
        except ValueError:
            logger.warning("not an event: %r", line)


async def _live(settings: Settings, wire: part.Wire, client: httpx.Client,
                held: dict) -> int:
    voice = await open_voice(settings)
    port = HttpPort(client, wire.base, wire)
    port.state(values_of(voice))
    speaker = _Speaker(voice, port)
    held["speaker"] = speaker
    listening = asyncio.create_task(voice.run(port))
    try:
        await listening
    finally:
        held.pop("speaker", None)
        listening.cancel()
        await asyncio.gather(listening, return_exceptions=True)
        await speaker.cancel()
        await voice.device.stop()
    return 0


async def run(settings: Settings | None = None, *,
              client: httpx.AsyncClient | None = None) -> int:
    settings = settings or Settings()
    host = socket.gethostname()
    # Events arrive before the voice is open; until then there is no mouth.
    held: dict = {}

    def on_event(event: dict) -> None:
        speaker = held.get("speaker")
        if speaker is not None:
            speaker.feed(event)

    async def work(wire: part.Wire) -> int:
        # The voice reports from its own loop without awaiting, so its port
        # speaks through a synchronous client holding the part's key.
        with httpx.Client(headers={"X-Heare-Token": wire.secret},
                          timeout=None) as sync:
            return await _live(settings, wire, sync, held)

    return await part.live(
        {"channel": CHANNEL, "forms": ["input", "output"],
         "about": "мікрофон і динамік", "key": f"voice@{host}", "host": host,
         "ui": UI},
        work, on_event=on_event, settings=settings, client=client)


def main() -> int:
    parser = argparse.ArgumentParser(prog="heare.voice")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
