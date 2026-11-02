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
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx

from heare import layout, logs
from heare import part
from heare.channels.voice.channel import Voice, open_voice
from heare.config import Settings
from heare.core import Message
from heare.core.i18n import N_

logger = logging.getLogger("heare.voice.body")

CHANNEL = "voice"
# The longest a report to the brain may hold the voice's loop.
REPORT_TIMEOUT = httpx.Timeout(3.0)
# Reports are made off the loop; this is how many may wait before new ones
# are dropped. A slow brain costs reports, never frames.
REPORT_QUEUE_S = 64
# A reply with no piece and no «said» for this long ended without telling
# us — a brain restarted mid-reply. The mouth is closed, not left muted.
REPLY_IDLE_S = 30.0
_END = object()

# What the person can touch on this body. The values here are the defaults
# the page shows until the voice is open and reports the real ones.
UI = [
    {"name": "mic", "kind": "toggle", "label": N_("Microphone"), "value": True},
    {"name": "speaker", "kind": "toggle", "label": N_("Sound"), "value": True},
    {"name": "volume", "kind": "slider", "label": N_("Volume"), "value": 1.0,
     "min": 0, "max": 1, "step": 0.05},
    {"name": "listen", "kind": "choice", "label": N_("Answers"), "value": "judge",
     "options": [{"value": "judge", "label": N_("decides itself")},
                 {"value": "name", "label": N_("only to its name")}]},
    {"name": "keep_heard", "kind": "toggle", "label": N_("Keep recordings"),
     "value": True},
    {"name": "mode", "kind": "label", "label": N_("State"), "value": N_("hearing"),
     "head": True},
]


# The three switches the device does not remember by itself. The channel
# keeps «listen» and «keep_heard» in settings already; these live here so a
# restarted voice comes back as it was left.
_SAVED = {"mic": "voice_mic", "speaker": "voice_speaker", "volume": "voice_volume"}


def _apply_saved(voice: Voice, settings: Settings) -> None:
    """Put the saved switches back on the device before it is reported."""
    for control, name in _SAVED.items():
        value = getattr(settings, name, "")
        if value == "":
            continue
        try:
            voice.set_control(control, value)
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("could not restore %s: %s", control, exc)


def values_of(voice: Voice) -> dict:
    """The controls' present worth, with the one line a person reads first."""
    values = dict(voice.controls)
    values["mode"] = N_("hearing") if values.get("mic") else N_("paused")
    return values


class HttpPort:
    """The core's port, spoken over the door.

    Reports that need no answer are queued and sent from a worker thread:
    the audio loop must keep reading frames while the brain is slow. A
    verdict («said_over») is the one call whose answer decides the turn,
    so it is made directly — on the recognition task, never on the audio
    loop.
    """

    def __init__(self, client: httpx.Client, base: str,
                 wire: part.Wire | None = None) -> None:
        self._client, self._base = client, base
        # The part's wire keeps the last values, to tell a brain that restarted.
        self._wire = wire
        self._queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue(
            maxsize=REPORT_QUEUE_S)
        self._worker: asyncio.Task | None = None
        self._dropped = 0

    def _post(self, path: str, payload: dict) -> httpx.Response | None:
        try:
            response = self._client.post(f"{self._base}{path}", json=payload)
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            logger.warning("the brain did not take %s: %s", path, exc)
            return None

    async def _drain_reports(self) -> None:
        while True:
            path, payload = await self._queue.get()
            try:
                if isinstance(self._client, httpx.AsyncClient):
                    await self._post_async(path, payload)
                else:
                    await asyncio.to_thread(self._post, path, payload)
            except Exception:  # noqa: BLE001 — one bad report is not the worker
                logger.exception("the report worker failed on %s", path)
            finally:
                self._queue.task_done()

    async def _post_async(self, path: str, payload: dict) -> httpx.Response | None:
        try:
            response = await self._client.post(
                f"{self._base}{path}", json=payload, timeout=REPORT_TIMEOUT)
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            logger.warning("the brain did not take %s: %s", path, exc)
            return None

    def _report(self, path: str, payload: dict) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No loop: a caller outside the part's life (a test, a CLI).
            self._post(path, payload)
            return
        try:
            self._queue.put_nowait((path, payload))
        except asyncio.QueueFull:
            self._dropped += 1
            logger.warning("the report queue is full — dropped %s (%d so far)",
                           path, self._dropped)
            return
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._drain_reports())

    async def drain(self) -> None:
        """Wait until every queued report has been attempted."""
        await self._queue.join()

    async def close(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None

    def started(self, channel: str) -> None:
        self._report("/api/input", {"channel": channel, "kind": "started"})

    def heard(self, message: Message) -> None:
        payload = {
            "channel": message.channel, "kind": "heard", "text": message.text,
            "addressed": message.addressed,
        }
        if message.speech_ms is not None:
            payload["speech_ms"] = message.speech_ms
        self._report("/api/input", payload)

    def nothing(self, channel: str) -> None:
        self._report("/api/input", {"channel": channel, "kind": "nothing"})

    def note(self, key: str, value: object) -> None:
        self._report("/api/input", {
            "channel": CHANNEL, "kind": "note", "key": key, "value": value,
        })

    def overheard(self, channel: str, text: str) -> None:
        self._report("/api/input", {
            "channel": channel, "kind": "overheard", "text": text,
        })

    def state(self, values: dict) -> None:
        if self._wire is not None:
            self._wire.values.update(values)
        self._report("/api/input", {
            "channel": CHANNEL, "kind": "state", "values": dict(values),
        })

    def sounding(self, on: bool) -> None:
        """Whether sound is still leaving the body — the brain waits on it."""
        if self._wire is not None:
            # A brain that restarts is told the last value it missed.
            self._wire.values.update({"speaking": bool(on)})
        self._report("/api/input", {
            "channel": CHANNEL, "kind": "state",
            "values": {"speaking": bool(on)},
        })

    def said_over(self, channel: str, text: str, speech_ms: float,
                  long: bool) -> bool | Awaitable[bool]:
        if isinstance(self._client, httpx.AsyncClient):
            return self._said_over_async(channel, text, speech_ms, long)
        response = self._post("/api/input/said_over", {
            "channel": channel, "text": text, "speech_ms": speech_ms,
            "long": long,
        })
        return self._verdict(response)

    async def _said_over_async(self, channel: str, text: str, speech_ms: float,
                               long: bool) -> bool:
        response = await self._post_async("/api/input/said_over", {
            "channel": channel, "text": text, "speech_ms": speech_ms,
            "long": long,
        })
        return self._verdict(response)

    @staticmethod
    def _verdict(response: httpx.Response | None) -> bool:
        if response is None:
            return False
        try:
            payload = response.json()
        except ValueError:
            logger.warning("the brain's said_over verdict was not JSON")
            return False
        if not isinstance(payload, dict):
            logger.warning("the brain's said_over verdict was not an object")
            return False
        # One malformed 200 is «not a turn», not the end of recognition.
        return bool(payload.get("turn", False))


class _Reply:
    """One reply's pieces, queued until the mouth is ready for them."""

    def __init__(self, voice: Voice, port: HttpPort | None = None, *,
                 idle_s: float = REPLY_IDLE_S,
                 on_done: Callable[[_Reply], None] | None = None) -> None:
        self._voice = voice
        self._port = port
        self._idle_s = idle_s
        self._on_done = on_done
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
            try:
                piece = await asyncio.wait_for(
                    self._queue.get(), timeout=self._idle_s)
            except asyncio.TimeoutError:
                # The brain went away between pieces: end the utterance
                # rather than hold the microphone muted forever.
                logger.warning("the reply ended without a «said»")
                return
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
            if self._on_done is not None:
                self._on_done(self)

    async def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)


class _Speaker:
    """What the brain streams, applied to one voice body."""

    def __init__(self, voice: Voice, port: HttpPort | None = None, *,
                 idle_s: float = REPLY_IDLE_S, settings: Settings | None = None) -> None:
        self._voice = voice
        self._port = port
        self._idle_s = idle_s
        self._settings = settings
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
            self._reply = _Reply(self._voice, self._port, idle_s=self._idle_s,
                                 on_done=self._finished)
        self._reply.piece(text)

    def _end(self) -> None:
        if self._reply is not None:
            self._reply.end()
            self._reply = None
            self._sounded = True

    def _finished(self, reply: _Reply) -> None:
        """A reply that timed out or was cancelled must not swallow the
        next one's pieces or leave its utterance holding the microphone."""
        if self._reply is reply:
            self._reply = None
            self._sounded = True

    async def reset(self) -> None:
        """The brain came back: end whatever it abandoned mid-reply.

        The old reply has no «said» coming, so waiting for one would keep
        the half-duplex microphone muted for the rest of the session. Its
        task is cancelled and its utterance closed; the next piece starts
        a new reply on a clean line.
        """
        reply, self._reply = self._reply, None
        self._sounded = False
        self._voice.stop()
        if reply is not None:
            await reply.cancel()

    def _turn(self, name: str, value) -> None:
        try:
            self._voice.set_control(name, value)
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("the voice refused %r: %s", name, exc)
        else:
            self._remember(name)
        if self._port is not None:
            self._port.state(values_of(self._voice))

    def _remember(self, name: str) -> None:
        setting = _SAVED.get(name)
        if setting is None or self._settings is None:
            return
        value = self._voice.controls.get(name)
        if value is None:
            return
        self._settings.set(setting, value if name == "volume" else bool(value))

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
    _apply_saved(voice, settings)
    port = HttpPort(client, wire.base, wire)
    port.state(values_of(voice))
    speaker = _Speaker(voice, port, settings=settings)
    held["speaker"] = speaker
    listening = asyncio.create_task(voice.run(port))
    try:
        await listening
    finally:
        held.pop("speaker", None)
        listening.cancel()
        await asyncio.gather(listening, return_exceptions=True)
        await speaker.cancel()
        await port.close()
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

    def on_reconnect() -> None:
        speaker = held.get("speaker")
        if speaker is not None:
            asyncio.create_task(speaker.reset())

    async def work(wire: part.Wire) -> int:
        # The voice reports from its own loop without awaiting, so its port
        # speaks through a synchronous client holding the part's key; the
        # port moves every report off the loop. A call that hangs holds the
        # whole loop — the stream unread, the brain not knocked on again —
        # so none may hang for long.
        with httpx.Client(headers={"X-Heare-Token": wire.secret},
                          timeout=REPORT_TIMEOUT) as sync:
            return await _live(settings, wire, sync, held)

    return await part.live(
        {"channel": CHANNEL, "forms": ["input", "output"],
         "about": "microphone and speaker", "key": f"voice@{host}", "host": host,
         "keys": ["GROQ_API_KEY"],
         "ui": UI},
        work, on_event=on_event, on_reconnect=on_reconnect,
        settings=settings, client=client)


def main() -> int:
    parser = argparse.ArgumentParser(prog="heare.voice")
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
