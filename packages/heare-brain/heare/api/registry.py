"""The parts the brain knows: who they are, whether they are trusted,
whether they are here, and what they may hear.

No HTTP here. The door asks and answers; the registry keeps the answers.
It was a dozen closures inside the door sharing two dicts, which is how
presence had to be woven through pairing and controls to be added at all.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger("heare.api.registry")

# How long a part waits for the person's answer before it leaves.
APPROVAL_WAIT_S = 120.0
# How long a closed page stays on the list as away. A window is not a part
# anyone installed: every tab ever opened would otherwise stay there.
WINDOW_GONE_S = 300.0


class Registry:
    def __init__(self, trusted_file: Path,
                 emit: Callable[..., object],
                 on_executor: Callable[[str, str], None] | None = None) -> None:
        """*emit* tells the page a part changed; *on_executor* is called
        when a part that does work is let in, with its channel and about."""
        self.bodies: dict[str, dict] = {}
        self._trusted_file = trusted_file
        self._emit = emit
        self._on_executor = on_executor
        self._waiting: dict[str, asyncio.Event] = {}
        # How many event streams each part holds open. A part is there while
        # it listens: a closed page or a stopped body ends its stream, and the
        # last stream to end makes it "away" — nothing else says it left.
        self._listening: dict[str, int] = {}
        self._fading: dict[str, asyncio.TimerHandle] = {}

    # -- trust ------------------------------------------------------------

    def trusted(self) -> set[str]:
        try:
            remembered = set(json.loads(self._trusted_file.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            remembered = set()
        # A folder the person put in the parts directory is a yes already:
        # nothing knocks its way in, it had to be carried there by hand.
        return remembered | {pid for pid, b in self.bodies.items()
                             if b.get("state") == "away"}

    def remember(self, pid: str) -> None:
        self._trusted_file.parent.mkdir(parents=True, exist_ok=True)
        self._trusted_file.write_text(
            json.dumps(sorted(self.trusted() | {pid}), ensure_ascii=False),
            encoding="utf-8",
        )

    def _untrust(self, pid: str) -> None:
        try:
            remembered = set(json.loads(self._trusted_file.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            remembered = set()
        remembered.discard(pid)
        self._trusted_file.write_text(
            json.dumps(sorted(remembered), ensure_ascii=False), encoding="utf-8")

    # -- the list ---------------------------------------------------------

    def attach(self, pid: str, channel: str, forms: list, about: str,
               host: str, state: str, ui: list | None = None,
               hears: str | None = None) -> None:
        old = self.bodies.get(pid) or {}
        self.bodies[pid] = {
            "channel": channel, "forms": forms, "about": about,
            "host": host, "state": state,
            # "all" for a part that shows the whole conversation (a chat);
            # any other part hears only its own channel.
            "hears": hears or old.get("hears") or "own",
            # What the part offers to the person, and what it last reported
            # those controls are worth. Both travel to the page untouched.
            "ui": list(ui if ui is not None else old.get("ui") or []),
            "values": dict(old.get("values") or {}),
        }
        if state == "approved" and "executor" in forms and self._on_executor:
            self._on_executor(channel, about)
        self._tell(self.bodies[pid])

    def load(self, parts_dir: Path) -> None:
        """Know every part with a manifest before it knocks: away, trusted."""
        try:
            files = sorted(parts_dir.glob("*/heare.json"))
        except OSError:
            return
        for path in files:
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                logger.warning("not a manifest: %s", path)
                continue
            if not isinstance(manifest, dict) or not manifest.get("channel"):
                continue
            channel = str(manifest["channel"])
            host = str(manifest.get("host") or socket.gethostname())
            self.attach(str(manifest.get("key") or f"{channel}@{host}"), channel,
                        list(manifest.get("forms") or []),
                        str(manifest.get("about") or ""), host, "away",
                        list(manifest.get("ui") or []))

    def by_channel(self, channel: str) -> dict | None:
        found = [b for b in self.bodies.values() if b.get("channel") == channel]
        approved = [b for b in found if b.get("state") == "approved"]
        chosen = (approved or found)[-1:] if found else []
        return chosen[0] if chosen else None

    def pid_of(self, entry: dict) -> str:
        return next(pid for pid, b in self.bodies.items() if b is entry)

    def channel_of(self, pid: str) -> str:
        entry = self.bodies.get(pid) or {}
        return str(entry.get("channel") or pid.split("@")[0])

    def forget(self, pid: str) -> dict | None:
        """Out of the list and out of trust; the entry it was, or None."""
        entry = self.bodies.pop(pid, None)
        if entry is None:
            return None
        self._untrust(pid)
        self._tell(entry, "forgotten")
        return entry

    # -- the person's answer ----------------------------------------------

    async def answer(self, pid: str) -> str:
        """Wait for the person to decide on a pending part: its state then."""
        event = self._waiting.setdefault(pid, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), APPROVAL_WAIT_S)
        except TimeoutError:
            self.bodies[pid]["state"] = "expired"
        finally:
            if self._waiting.get(pid) is event:
                self._waiting.pop(pid, None)
        return self.bodies[pid]["state"]

    def decide(self, pid: str, state: str) -> bool:
        """The person's yes or no; False when nothing of that name waits."""
        entry = self.bodies.get(pid)
        if entry is None or entry.get("state") != "pending":
            return False
        entry["state"] = state
        if state == "approved":
            self.remember(pid)
        event = self._waiting.get(pid)
        if event is not None:
            event.set()
        return True

    # -- presence ---------------------------------------------------------

    def listens(self, pid: str) -> None:
        """A stream opened. A part that listens again is there again,
        whatever its knock is doing: a page reloaded after a brain restart
        knocks before the brain notices its old stream ended, and that end
        must not have the last word."""
        self._listening[pid] = self._listening.get(pid, 0) + 1
        if pid in self._fading:
            self._fading.pop(pid).cancel()
        entry = self.bodies.get(pid)
        if entry is not None and entry.get("state") == "away":
            entry["state"] = "approved"
            self._tell(entry)

    def left(self, pid: str) -> None:
        """A stream ended; the last one makes the part away."""
        self._listening[pid] -= 1
        if self._listening[pid]:
            return
        del self._listening[pid]
        entry = self.bodies.get(pid)
        if entry is None or entry.get("state") != "approved":
            return
        entry["state"] = "away"
        self._tell(entry)
        if pid.startswith("window@"):
            self._fading[pid] = asyncio.get_running_loop().call_later(
                WINDOW_GONE_S, self._drop, pid)

    def _drop(self, pid: str) -> None:
        """A window away long enough leaves the list; its key stays, so the
        page comes back by knocking."""
        self._fading.pop(pid, None)
        entry = self.bodies.get(pid)
        if entry is None or entry.get("state") != "away":
            return
        del self.bodies[pid]
        self._tell(entry, "forgotten")

    # -- hearing ----------------------------------------------------------

    def hears(self, pid: str | None, data: dict) -> bool:
        """Whether a listener may hear an event with *data*.

        The person — the master key, a window — hears everything (*pid* is
        None). A part hears its own channel and what is addressed to it,
        unless it was let in as one that shows the whole conversation: the
        conversation is the person's, not every trusted part's."""
        if pid is None:
            return True
        entry = self.bodies.get(pid)
        if entry is None:
            return False
        if entry.get("hears") == "all":
            return True
        return data.get("channel") == entry["channel"] or data.get("part") == pid

    def _tell(self, entry: dict, state: str | None = None) -> None:
        self._emit("part", channel=entry["channel"], forms=entry["forms"],
                   about=entry["about"], host=entry["host"],
                   state=state or entry["state"])
