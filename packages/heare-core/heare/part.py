"""The wire every body uses: the brain's stream, and one report of itself."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from urllib.parse import quote

import httpx

logger = logging.getLogger("heare.part")

RETRY_S = 1.0


async def follow(base: str, secret: str, channel: str,
                 on_event: Callable[[dict], None],
                 on_open: Callable[[], None] | None = None) -> None:
    """Read the brain's stream forever, handing this channel's events over.

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
                        if event.get("channel") == channel:
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
