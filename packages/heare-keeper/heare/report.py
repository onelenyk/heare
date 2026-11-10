"""What this machine tells the brain: its parts, its load, and what to run.

The brain keeps the desired state, the keeper the actual one. Every half
minute the keeper posts what it has and turns manifests to match the answer.
The brain never writes a manifest: a machine's parts are its own, and a
switch on the page is a wish until the keeper has made it true.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import sys
from pathlib import Path

import httpx

from heare import machine, part
from heare.config import HOME, Settings

logger = logging.getLogger("heare.keeper.report")

# The page's switch is not a keystroke: half a minute is soon enough, and a
# machine that was asleep catches up on its next word.
EVERY_S = 30.0
TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def describe(folder: Path) -> dict:
    """What this machine has, as one report."""
    try:
        total, _used, free = shutil.disk_usage(HOME)
    except OSError:
        total = free = None
    try:
        load = [round(value, 2) for value in os.getloadavg()]
    except (OSError, AttributeError):
        load = []
    return {
        "version": part.installed() or "",
        "platform": sys.platform,
        "arch": platform.machine(),
        "load": load,
        "disk_free": free,
        "disk_total": total,
        "parts": [
            {"name": one["name"], "label": one["label"], "what": one["what"],
             "channel": one["channel"], "on": bool(one["on"]),
             "state": str(one["state"]), "why": str(one["why"])}
            for one in machine.parts(folder)
        ],
    }


def apply(folder: Path, desired: object) -> list[str]:
    """Turn manifests to match the person's wish; the parts turned.

    A name this machine does not know is ignored — the brain may be newer
    than the machine — and a part it cannot run is logged once, not retried
    into a loop: the report will show it as off with the reason.
    """
    if not isinstance(desired, dict):
        return []
    running = machine.running(folder)
    turned = []
    for name, on in desired.items():
        if not isinstance(on, bool) or name not in machine.PARTS:
            continue
        if (name in running) == on:
            continue
        try:
            machine.turn(folder, name, on)
        except (ValueError, OSError) as exc:
            logger.warning("could not turn %s %s: %s", name,
                           "on" if on else "off", exc)
            continue
        turned.append(name)
        logger.warning("%s: %s by the person", name, "on" if on else "off")
    return turned


class Report:
    """The keeper's half of the conversation, once every half minute."""

    def __init__(self, settings: Settings, folder: Path) -> None:
        self.settings = settings
        self.folder = Path(folder)

    async def run(self) -> None:
        while True:
            try:
                await self.once()
            except (httpx.HTTPError, OSError, ValueError) as exc:
                logger.debug("the brain did not take the report: %s", exc)
            await asyncio.sleep(EVERY_S)

    async def once(self) -> None:
        data = describe(self.folder)
        data["name"] = self.settings.host
        async with httpx.AsyncClient(timeout=TIMEOUT) as brain:
            answer = await brain.post(
                f"{self.settings.base}/api/machine/mine/report",
                headers={"X-Heare-Token": self.settings.secret}, json=data)
        if answer.status_code >= 400:
            logger.debug("the brain did not take the report: %s", answer.status_code)
            return
        body = answer.json() if answer.headers.get("Content-Type", "").startswith("application/json") else {}
        apply(self.folder, (body or {}).get("desired"))
