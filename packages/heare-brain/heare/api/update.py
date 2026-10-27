"""A newer heare: found, fetched, checked, and handed to the app to put in.

The brain asks the release feed (GitHub's latest release, unless the
settings name another) whether there is a version above its own. Asked to
install, it downloads this Mac's dmg, checks it against the release's
SHA256SUMS.txt, makes a backup of the memory, and says ``installing`` in
the state. It cannot swap the app it runs inside; the app reads the state
every few seconds, sees that, and does the swap and the restart itself.

A brain not started by the app (a checkout, a server) offers the release
page instead: nobody would be there to swap anything.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import re
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from heare.api import backup
from heare.core.i18n import _

logger = logging.getLogger("heare.api.update")

FEED = "https://api.github.com/repos/onelenyk/heare/releases/latest"
SUMS = "SHA256SUMS.txt"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
# What a release note is cut to on the page.
NOTES_CHARS = 600


class UpdateError(RuntimeError):
    """Why an update could not go on, said for the owner."""


def version_of(text: str | None) -> tuple[int, ...] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", (text or "").strip())
    return tuple(int(n) for n in match.groups()) if match else None


def dmg_name(arch: str | None = None) -> str:
    """heare-arm64.dmg or heare-x86_64.dmg: CI names each for its Mac."""
    return f"heare-{arch or platform.machine()}.dmg"


def read_release(release: dict, current: str | None, arch: str | None = None) -> dict:
    """What a release says to this Mac: is it newer, and where its dmg and
    its sums are. Plain data, no network."""
    latest = str(release.get("tag_name") or "").lstrip("v")
    assets = {str(a.get("name")): str(a.get("browser_download_url") or "")
              for a in release.get("assets") or [] if isinstance(a, dict)}
    mine, have = version_of(latest), version_of(current)
    return {
        "latest": latest or None,
        "newer": bool(mine and have and mine > have),
        "notes": str(release.get("body") or "").strip()[:NOTES_CHARS],
        "page": str(release.get("html_url") or ""),
        "dmg": assets.get(dmg_name(arch)) or None,
        "sums": assets.get(SUMS) or None,
    }


def expected_sum(sums: str, name: str) -> str | None:
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name and re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            return parts[0]
    return None


class Updates:
    def __init__(self, settings, current: str | None, home: Path,
                 emit: Callable[..., None] = lambda *a, **k: None,
                 arch: str | None = None) -> None:
        self._settings = settings
        self.current = current
        self.home = home
        self._emit = emit
        self.arch = arch or platform.machine()
        # The app is the one that can swap itself; it names itself here.
        self.under_app = bool(os.environ.get("HEARE_PARENT_PID"))
        self.folder = home / "cache" / "update"
        self._release: dict = {}
        self.state: dict = {"state": "idle", "current": current, "can_install": self.under_app}
        done = self._installed()
        if done:
            self.state.update(state="updated", previous=done.get("from"))

    def _installed(self) -> dict | None:
        """The update that brought this version, if it just did."""
        try:
            done = json.loads((self.folder / "installing.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if done.get("to") != self.current:
            return None
        (self.folder / "installing.json").unlink(missing_ok=True)
        return done

    def _set(self, **fields) -> dict:
        self.state.update(fields)
        self._emit("update", **self.state)
        return dict(self.state)

    @property
    def feed(self) -> str:
        return str(getattr(self._settings, "update_feed", "") or FEED)

    async def check(self) -> dict:
        if self.state["state"] in ("downloading", "installing"):
            return dict(self.state)
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True,
                                         headers={"Accept": "application/vnd.github+json"}) as web:
                answer = await web.get(self.feed)
                answer.raise_for_status()
                release = answer.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("could not ask for a newer heare: %s", exc)
            return self._set(checked=time.time(), error=_("could not ask about a new version"))
        found = read_release(release if isinstance(release, dict) else {}, self.current, self.arch)
        self._release = found
        state = "available" if found["newer"] else (
            "updated" if self.state["state"] == "updated" else "current")
        return self._set(state=state, latest=found["latest"], notes=found["notes"],
                         page=found["page"], checked=time.time(), error=None)

    async def install(self) -> dict:
        """Fetch, check, back up; then the app takes it from here."""
        if self.state["state"] != "available":
            raise UpdateError(_("there is no new version — check first"))
        if not self.under_app:
            raise UpdateError(_("this brain was not started by the app — install the new version from the release page"))
        release = self._release
        if not release.get("dmg") or not release.get("sums"):
            raise UpdateError(_("the release has no {dmg} or {sums}").format(dmg=dmg_name(self.arch), sums=SUMS))
        self._set(state="downloading", error=None)
        try:
            path = await self._fetch(release)
            self._set(state="backing_up")
            await asyncio.to_thread(backup.make, self.home, sound=False, version=self.current)
        except (UpdateError, httpx.HTTPError, OSError) as exc:
            logger.warning("the update stopped: %s", exc)
            why = str(exc) if isinstance(exc, UpdateError) else _("could not download the new version")
            return self._set(state="failed", error=why)
        (self.folder / "installing.json").write_text(json.dumps(
            {"from": self.current, "to": release["latest"]}), encoding="utf-8")
        return self._set(state="installing", dmg=str(path), version=release["latest"])

    async def _fetch(self, release: dict) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        target = self.folder / f"heare-{release['latest']}-{self.arch}.dmg"
        partial = target.with_suffix(".part")
        digest = hashlib.sha256()
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as web:
            sums = await web.get(release["sums"])
            sums.raise_for_status()
            wanted = expected_sum(sums.text, dmg_name(self.arch))
            if wanted is None:
                raise UpdateError(_("{sums} has no sum for {dmg}").format(sums=SUMS, dmg=dmg_name(self.arch)))
            async with web.stream("GET", release["dmg"]) as answer:
                answer.raise_for_status()
                with partial.open("wb") as out:
                    async for chunk in answer.aiter_bytes(1 << 20):
                        digest.update(chunk)
                        out.write(chunk)
        if digest.hexdigest() != wanted:
            partial.unlink(missing_ok=True)
            raise UpdateError(_("the download does not match the release sum — not installing"))
        os.replace(partial, target)
        return target

    def failed(self, why: str) -> dict:
        """The app could not put the new version in."""
        (self.folder / "installing.json").unlink(missing_ok=True)
        return self._set(state="failed", error=why[:300] or _("the app could not install the new version"))

