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
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import httpx

from heare import machine, part
from heare.config import HOME, Settings
from heare.core.i18n import _

logger = logging.getLogger("heare.keeper.report")

# The page's switch is not a keystroke: half a minute is soon enough, and a
# machine that was asleep catches up on its next word.
EVERY_S = 30.0
# A brain that is away, or has forgotten this machine, is asked less often:
# a refused report every half minute is a busy loop, not a heartbeat.
BACKOFF_MAX_S = 5 * 60.0
TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def wait_after(failures: int) -> float:
    """How long to wait after this many refusals in a row."""
    return min(EVERY_S * (2 ** max(0, failures)), BACKOFF_MAX_S)


def _sysctl(name: str) -> int | None:
    try:
        answer = subprocess.run(["sysctl", "-n", name], capture_output=True,
                                text=True, timeout=2, check=False)
        return int(answer.stdout.strip()) if answer.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _memory() -> tuple[int | None, int | None]:
    """Free and total RAM, in bytes; None where the machine will not say.

    Linux answers in ``/proc/meminfo``; a Mac in two sysctls, and has no
    ``SC_AVPHYS_PAGES`` to ask for the free half.
    """
    if sys.platform.startswith("linux"):
        try:
            info = {}
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                key, _, rest = line.partition(":")
                info[key] = int(rest.split()[0]) * 1024
            return info.get("MemAvailable"), info.get("MemTotal")
        except (OSError, ValueError, IndexError):
            return None, None
    total = _sysctl("hw.memsize")
    pages = _sysctl("vm.page_free_count")
    try:
        page = os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError):
        page = None
    free = pages * page if pages is not None and page else None
    return free, total


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
    mem_free, mem_total = _memory()
    return {
        "version": part.installed() or "",
        "platform": sys.platform,
        "arch": platform.machine(),
        "load": load,
        "disk_free": free,
        "disk_total": total,
        "mem_free": mem_free,
        "mem_total": mem_total,
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

    def __init__(self, settings: Settings, folder: Path, *,
                 on_restart: Callable[[str], None] | None = None,
                 on_refused: Callable[[str], None] | None = None) -> None:
        self.settings = settings
        self.folder = Path(folder)
        self._on_restart = on_restart
        self._on_refused = on_refused

    async def run(self) -> None:
        failures = 0
        while True:
            try:
                taken = await self.once()
            except (httpx.HTTPError, OSError, ValueError) as exc:
                logger.debug("the brain did not take the report: %s", exc)
                taken = False
            failures = 0 if taken else failures + 1
            await asyncio.sleep(wait_after(failures))

    async def once(self) -> bool:
        data = describe(self.folder)
        data["name"] = self.settings.host
        async with httpx.AsyncClient(timeout=TIMEOUT) as brain:
            answer = await brain.post(
                f"{self.settings.base}/api/machine/mine/report",
                headers={"X-Heare-Token": self.settings.secret}, json=data)
        if answer.status_code in (401, 403):
            # The brain will not know this machine until it is paired
            # again: no retry can fix a key that went with the trust.
            if self._on_refused is not None:
                self._on_refused(
                    _("the brain no longer knows this machine — pair it again"))
        if answer.status_code >= 400:
            logger.debug("the brain did not take the report: %s", answer.status_code)
            return False
        body = answer.json() if answer.headers.get("Content-Type", "").startswith("application/json") else {}
        apply(self.folder, (body or {}).get("desired"))
        again = (body or {}).get("restart")
        if self._on_restart is not None and isinstance(again, list):
            for name in again:
                if isinstance(name, str) and name in machine.PARTS:
                    self._on_restart(name)
        return True
