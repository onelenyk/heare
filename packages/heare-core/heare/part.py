"""How a part lives: it knocks, listens, reports, and outlives the brain.

Every part had its own copy of this life — knock, take the key, report,
follow the stream, knock again when it comes back — and the copies drifted:
one did not reconnect, one fell on a call the brain dropped while it
restarted. ``live`` is that life once; a part brings what it is and the
work it does.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Awaitable, Callable
from importlib import metadata
from urllib.parse import quote

import httpx

from heare import logs
from heare.config import Settings
from heare.core import PROTOCOL

logger = logging.getLogger("heare.part")

RETRY_S = 1.0
# A knock answered later than this is waiting for the person; only then is
# that worth a line.
ASKING_S = 2.0


def installed() -> str | None:
    """The version a part knocks with: that of the core it lives by.

    Every distribution is released at one version, and the core cannot tell
    which part called it; a part that knows better says ``"version"`` in
    its about, and that wins.
    """
    try:
        return metadata.version("heare-core")
    except metadata.PackageNotFoundError:
        return None


async def knock(client: httpx.AsyncClient | httpx.Client, base: str,
                about: dict) -> dict:
    """Knock until the brain answers; say only what the person must know.

    A brain that is not up yet is waited for, not a reason to die: the
    keeper starts the brain and the parts together, and a part that fell
    on that race was only started again. A knock trusted at once says
    nothing; one still unanswered after a moment is waiting for the person
    on the page, and that is said once.
    """
    url = f"{base}/api/bodies"
    said_down = False
    while True:
        asking = asyncio.ensure_future(
            client.post(url, json=about) if isinstance(client, httpx.AsyncClient)
            else asyncio.to_thread(client.post, url, json=about))
        try:
            done, _ = await asyncio.wait({asking}, timeout=ASKING_S)
            if not done:
                logs.say("чекаю, доки мене одобрять на сторінці…")
            answer = await asking
        except (httpx.ConnectError, httpx.ConnectTimeout):
            if not said_down:
                logs.say(f"мозок ще не відповідає на {base} — чекаю")
                said_down = True
            await asyncio.sleep(RETRY_S)
            continue
        answer.raise_for_status()
        return answer.json()


async def follow(base: str, secret: str, channel: str | None,
                 on_event: Callable[[dict], None],
                 on_open: Callable[[], None] | None = None) -> None:
    """Read the brain's stream forever, handing this channel's events over
    (every event, when *channel* is None).

    A brain that restarts is not a reason to die: the stream is opened
    again a second later, which is what makes a body a beacon.
    """
    while True:
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "GET", f"{base}/api/events?token={quote(secret, safe='')}"
                ) as response:
                    if on_open is not None:
                        try:
                            on_open()
                        except Exception:  # noqa: BLE001 — the stream will try again
                            logger.exception("the knock on reconnect failed")
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        try:
                            event = json.loads(line[6:])
                        except ValueError:
                            continue
                        if channel is None or event.get("channel") == channel:
                            on_event(event)
        except (httpx.HTTPError, OSError) as exc:
            logger.warning("the stream broke: %s", exc)
        await asyncio.sleep(RETRY_S)


async def report(client: httpx.AsyncClient, base: str, channel: str,
                 values: dict) -> None:
    """Tell the brain what the part's controls are worth now."""
    try:
        answer = await client.post(f"{base}/api/input", json={
            "channel": channel, "kind": "state", "values": dict(values)})
        answer.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("could not report the state: %s", exc)


class Wire:
    """A part's line to the brain, once the part is let in."""

    def __init__(self, client: httpx.AsyncClient, base: str, channel: str,
                 pid: str, secret: str, name: str = "") -> None:
        self.client, self.base = client, base
        self.channel, self.pid, self.secret = channel, pid, secret
        # What the assistant is called, as the brain said when it let us in.
        self.name = name
        self.values: dict = {}
        self._sending: set[asyncio.Task] = set()

    async def post(self, path: str, body: dict | None = None) -> httpx.Response:
        """A call that outlives a brain restart: while the brain is away the
        call waits and is made again, instead of ending the part."""
        said = False
        while True:
            try:
                return await self.client.post(f"{self.base}{path}", json=body)
            except httpx.TransportError as exc:
                if not said:
                    logger.warning("the brain is away (%s); waiting", exc)
                    said = True
                await asyncio.sleep(RETRY_S)

    def report(self, values: dict | None = None) -> None:
        """Tell the brain what the controls are worth, without waiting.
        The last values are kept: a brain that restarts is told them again."""
        if values is not None:
            self.values.update(values)
        task = asyncio.create_task(
            report(self.client, self.base, self.channel, self.values))
        self._sending.add(task)
        task.add_done_callback(self._sending.discard)

    async def settle(self) -> None:
        await asyncio.gather(*self._sending, return_exceptions=True)


async def live(about: dict, work: Callable[[Wire], Awaitable[int | None]], *,
               on_event: Callable[[dict], None] | None = None,
               hello: str = "", settings: Settings | None = None,
               client: httpx.AsyncClient | None = None) -> int:
    """Let a part in and keep it in while *work* runs; the part's exit code.

    *about* is the knock: channel, forms, about, key, host, ui, and
    ``"hears": "all"`` for a part that shows the whole conversation. Events
    of the part's channel go to *on_event*. Each time the stream comes back
    the part knocks again and repeats its last report, so a brain that
    restarted knows it — without the part noticing anything.
    """
    settings = settings or Settings()
    base = settings.base
    try:
        secret = settings.secret
    except OSError:
        print(f"немає ключа ({settings.api_token_file}) — мозок запущений?",
              file=sys.stderr)
        return 1
    about = {"v": PROTOCOL, "version": installed(), **about}
    owned = client is None
    client = client or httpx.AsyncClient(timeout=None)
    client.headers["X-Heare-Token"] = secret
    try:
        try:
            answer = await knock(client, base, about)
        except httpx.HTTPError as exc:
            logs.say(f"мозок не відповідає на {base}: {exc}")
            return 1
        if not answer.get("approved"):
            logs.say(f"мозок не одобрив: {answer.get('why') or 'відхилено'}")
            return 1
        if answer.get("token"):
            secret = answer["token"]
            client.headers["X-Heare-Token"] = secret
        if hello:
            logs.say(hello)
        wire = Wire(client, base, about["channel"],
                    str(answer.get("pid") or about.get("key") or ""), secret,
                    str(answer.get("name") or ""))
        opened = False

        async def again() -> None:
            try:
                await client.post(f"{base}/api/bodies", json=about)
            except httpx.HTTPError as exc:
                logger.warning("could not knock again: %s", exc)
                return
            if wire.values:
                wire.report()

        def on_open() -> None:
            nonlocal opened
            if opened:
                asyncio.create_task(again())
            opened = True

        channel = None if about.get("hears") == "all" else about["channel"]
        following = asyncio.create_task(follow(
            base, secret, channel, on_event or (lambda event: None), on_open))
        try:
            return int(await work(wire) or 0)
        finally:
            following.cancel()
            await asyncio.gather(following, return_exceptions=True)
            await wire.settle()
    finally:
        if owned:
            await client.aclose()
