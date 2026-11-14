"""A machine without the app takes a new heare when its brain says so.

The Mac's app swaps itself; a Linux machine has the keeper and nothing
else, so the keeper asks the brain, once a minute, whether the owner has
pressed «Update» for it. The brain names the release's install.sh and its
sums. The keeper checks the script against them and runs it the way the
machine was installed: install.sh stages every tool, keeps the ones it
replaces, and restarts the keeper and the brain.

It runs in a unit of its own (``systemd-run``): install.sh restarts this
keeper, and a child of the keeper would die with it halfway.

After the restart the new keeper reads the mark it left. The brain answers:
the update is done. The brain does not, or the version is still the old
one: the previous release is put back the same way, and the brain is told
why — it may be the one that did not come back, so the word waits until it
answers again.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path

import httpx

from heare import part
from heare.config import Settings
from heare.core.i18n import _

logger = logging.getLogger("heare.keeper.updating")

ASK_EVERY_S = 60.0
# A machine with auto-update on looks at GitHub itself, not only at what the
# brain names: a release goes live the moment it is published, and a machine
# that was asleep catches up the next time it looks. Once per five minutes
# keeps the unauthenticated GitHub call under its sixty-an-hour.
AUTO_EVERY_S = 300.0
# The brain and the parts have this long to answer after an update.
SETTLE_S = 120.0
RELEASES = "https://github.com/onelenyk/heare/releases/download/"
LATEST = "https://api.github.com/repos/onelenyk/heare/releases/latest"
TIMEOUT = httpx.Timeout(60.0, connect=10.0)
HEADERS = {"User-Agent": "heare"}


def wanted(answer: object) -> dict | None:
    """What the brain said, if it is a release of this repository to take."""
    if not isinstance(answer, dict) or answer.get("kind") != "wheels":
        return None
    version = str(answer.get("version") or "")
    script, sums, source = (str(answer.get(k) or "") for k in ("install", "sums", "from"))
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        return None
    if not all(url.startswith(RELEASES) for url in (script, sums, source)):
        return None
    if not script.endswith(f"/v{version}/install.sh") or source != script.rsplit("/", 1)[0]:
        return None
    return {"version": version, "install": script, "sums": sums, "from": source}


def sum_for(sums: str, name: str) -> str | None:
    for line in sums.splitlines():
        bits = line.split()
        if len(bits) == 2 and bits[1].lstrip("*") == name and re.fullmatch(r"[0-9a-f]{64}", bits[0]):
            return bits[0]
    return None


def _version(text: str) -> tuple[int, ...] | None:
    if not re.fullmatch(r"\d+\.\d+\.\d+", text):
        return None
    return tuple(int(part) for part in text.split("."))


def newer(candidate: str, installed: str) -> bool:
    """Whether *candidate* is a release above what this machine runs."""
    have, want = _version(installed), _version(candidate)
    return want is not None and (have is None or want > have)


def release_of(payload: object, installed: str) -> dict | None:
    """The release GitHub names, if it is one a machine may take by itself.

    Only a published, stable tag above the running version: a draft or a
    pre-release is somebody's experiment, and an older tag is a machine
    that is ahead of the latest (a rollback), not behind.
    """
    if not isinstance(payload, dict) or payload.get("draft") or payload.get("prerelease"):
        return None
    tag = str(payload.get("tag_name") or "")
    version = tag[1:] if tag.startswith("v") else ""
    if not newer(version, installed):
        return None
    source = f"{RELEASES}v{version}"
    return {"version": version, "install": f"{source}/install.sh",
            "sums": f"{source}/SHA256SUMS.txt", "from": source}


def command(script: Path, source: str, version: str, unit_ok: bool) -> list[str]:
    """install.sh in a unit of its own where systemd is, so it outlives the
    keeper it restarts."""
    run = ["sh", str(script), "--from", source, "--version", version]
    if unit_ok:
        return ["systemd-run", "--user", "--collect", "--quiet",
                f"--unit=heare-update-{version.replace('.', '-')}-{int(time.time())}", *run]
    return run


class Updating:
    def __init__(self, settings: Settings, home: Path) -> None:
        self.settings = settings
        self.folder = home / "cache" / "update"
        self.mark = self.folder / "keeper.json"
        # A release this machine took and could not keep: it stays refused
        # until a newer one is published, or the owner asks for it again.
        self.refused = self.folder / "auto-refused.json"
        self.looked = 0.0

    @property
    def here(self) -> bool:
        """Only a Linux machine the app does not hold: the app swaps a Mac."""
        return sys.platform.startswith("linux") and not os.environ.get("HEARE_PARENT_PID")

    @property
    def auto(self) -> bool:
        """Whether this machine takes a new release as soon as it is out.

        Off by default: a machine follows the owner's «Update» on the pult
        unless it was told to follow the repository itself.
        """
        return str(getattr(self.settings, "auto_update", "")).lower() in ("1", "true", "yes", "on")

    async def run(self) -> None:
        if not self.here:
            return
        await self.settle()
        while True:
            await asyncio.sleep(ASK_EVERY_S)
            try:
                await self.once()
            except (httpx.HTTPError, OSError, ValueError) as exc:
                logger.warning("could not ask the brain about an update: %s", part.broke(exc, None, None, 0))
            try:
                await self.maybe_auto()
            except (httpx.HTTPError, OSError, ValueError) as exc:
                logger.warning("could not look for a release: %s", part.broke(exc, None, None, 0))

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True,
                                 headers={**HEADERS, "X-Heare-Token": self.settings.secret})

    async def once(self) -> None:
        async with self._client() as brain:
            answer = await brain.get(f"{self.settings.base}/api/update/mine",
                                     params={"version": part.installed() or "", "arch": "linux"})
            answer.raise_for_status()
            asked = wanted(answer.json())
        if asked is None:
            return
        await self.take(asked, back_to=part.installed() or "")

    async def maybe_auto(self) -> None:
        """Take the newest published release, when this machine follows them.

        The brain still owns the pult's «Update»: this is only the machine
        listening to the repository, so a release lands without a person at
        the page. The same checks and the same rollback as an asked update.
        """
        if not self.auto or self.mark.exists():
            return
        if time.monotonic() - self.looked < AUTO_EVERY_S:
            return
        self.looked = time.monotonic()
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True,
                                     headers={**HEADERS, "Accept": "application/vnd.github+json"}) as web:
            answer = await web.get(LATEST)
            answer.raise_for_status()
            asked = release_of(answer.json(), part.installed() or "")
        if asked is None or asked["version"] == self.refused_version():
            return
        logger.warning("heare %s is published: taking it by itself", asked["version"])
        await self.take(asked, back_to=part.installed() or "")

    def refused_version(self) -> str:
        try:
            return str(json.loads(self.refused.read_text(encoding="utf-8")).get("version") or "")
        except (OSError, ValueError):
            return ""

    async def take(self, asked: dict, back_to: str, **mark: object) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        script = self.folder / f"install-{asked['version']}.sh"
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, headers=HEADERS) as web:
                sums = await web.get(asked["sums"])
                sums.raise_for_status()
                body = await web.get(asked["install"])
                body.raise_for_status()
        except httpx.HTTPError:
            await self.say_failed(_("could not download the new version"))
            return
        expected = sum_for(sums.text, "install.sh")
        if expected is None or hashlib.sha256(body.content).hexdigest() != expected:
            await self.say_failed(_("the download does not match the release sum — not installing"))
            return
        script.write_bytes(body.content)
        self.mark.write_text(json.dumps({
            "to": asked["version"], "back": back_to, "from": asked["from"],
            "at": time.time(), **mark}), encoding="utf-8")
        logger.warning("taking heare %s (from %s)", asked["version"], back_to or "?")
        process = await asyncio.create_subprocess_exec(
            *command(script, asked["from"], asked["version"], bool(shutil.which("systemd-run"))),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _out, err = await process.communicate()
        if process.returncode:
            self.mark.unlink(missing_ok=True)
            await self.say_failed((err or b"").decode(errors="replace").strip().splitlines()[-1:][0]
                                  if err and err.strip() else _("install.sh failed"))

    async def settle(self) -> None:
        """After an update: the version is the new one and the brain answers,
        or the previous release goes back in."""
        try:
            mark = json.loads(self.mark.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if mark.get("rolled_back"):
            failed = str(mark.get("failed") or mark.get("to") or "")
            if re.fullmatch(r"\d+\.\d+\.\d+", failed):
                # A machine following the repository must not put the same
                # broken release back every five minutes: it waits for one
                # above it, or for the owner to ask again.
                self.refused.parent.mkdir(parents=True, exist_ok=True)
                self.refused.write_text(json.dumps({
                    "version": failed, "why": str(mark.get("why") or ""),
                    "at": time.time()}), encoding="utf-8")
            self.mark.unlink(missing_ok=True)
            await self.say_failed(str(mark.get("why") or _("the new version did not start")), patient=True)
            return
        why = None
        if part.installed() != mark.get("to"):
            why = _("the new version did not install")
        elif not await self._brain_answers():
            why = _("the brain did not answer after the update")
        if why is None:
            self.mark.unlink(missing_ok=True)
            logger.warning("heare %s is in", mark.get("to"))
            return
        back = str(mark.get("back") or "")
        if not re.fullmatch(r"\d+\.\d+\.\d+", back) or back == part.installed():
            self.mark.unlink(missing_ok=True)
            await self.say_failed(why, patient=True)
            return
        logger.warning("%s; putting %s back", why, back)
        source = f"{RELEASES}v{back}"
        await self.take({"version": back, "install": f"{source}/install.sh",
                         "sums": f"{source}/SHA256SUMS.txt", "from": source},
                        back_to="", rolled_back=True, why=why,
                        failed=str(mark.get("to") or ""))

    async def _brain_answers(self) -> bool:
        deadline = time.monotonic() + SETTLE_S
        while time.monotonic() < deadline:
            try:
                async with self._client() as brain:
                    if (await brain.get(f"{self.settings.base}/health")).status_code == 200:
                        return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(5.0)
        return False

    async def say_failed(self, why: str, patient: bool = False) -> None:
        logger.warning("the update failed: %s", why)
        for _try in range(int(SETTLE_S // 5) if patient else 1):
            try:
                async with self._client() as brain:
                    answer = await brain.post(f"{self.settings.base}/api/update/mine/failed", json={"why": why})
                    if answer.status_code < 500:
                        return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(5.0)
