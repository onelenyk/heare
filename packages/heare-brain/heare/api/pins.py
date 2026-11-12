"""Pins: an anchor, not a copy.

A pinned message stays in its session; the pin only says it matters. Losing
the chip loses no words, and deleting a session deletes its pins with it.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path

logger = logging.getLogger("heare.api.pins")

PER_SESSION = 12
TOTAL = 40
CLAIM_TTL = 120.0
_ID = re.compile(r"^[0-9a-f]{32}/\d+$")


def describe(row: dict) -> tuple[str, str, str]:
    """What a chip says when neither the model nor the person said it."""
    text = str(row.get("text") or "").strip()
    artifact = row.get("artifact") or {}
    kind = str(artifact.get("label") or row.get("kind") or "text")
    title = str(artifact.get("title") or artifact.get("name")
                or (text.splitlines()[0][:46] if text else "")).strip()
    line = str(artifact.get("line") or "").strip()
    if not line and text:
        line = text.splitlines()[0][:60]
    return title or "A message", line, kind


class PinStore:
    """The one global file: the shelf is other sessions' pins."""

    def __init__(self, path: Path | str, emit=None) -> None:
        self._path = Path(path)
        self._emit = emit
        self._sessions = None
        self._pins: list[dict] = []
        self._load()

    # -- wiring -----------------------------------------------------------

    def bind_sessions(self, sessions) -> None:
        self._sessions = sessions

    # -- storage ----------------------------------------------------------

    def _load(self) -> None:
        try:
            saved = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        found = saved.get("pins") if isinstance(saved, dict) else None
        if isinstance(found, list):
            self._pins = [row for row in found if isinstance(row, dict) and row.get("id")]

    def _keep(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"v": 1, "pins": self._pins}, ensure_ascii=False),
                encoding="utf-8")
        except OSError:
            logger.exception("a pin that cannot be saved is still shown")

    def _announce(self, op: str, data: dict) -> None:
        if self._emit is None:
            return
        try:
            self._emit("pin", op=op, **data)
        except Exception:
            logger.exception("the pin event failed")

    def _row(self, message: str) -> dict | None:
        if self._sessions is None or not message:
            return None
        session, _, n = message.rpartition("/")
        if not session or not n.isdigit():
            return None
        return self._sessions.message(session, int(n))

    def _expire(self) -> None:
        cutoff = time.time() - CLAIM_TTL
        stale = [p for p in self._pins
                 if p.get("self") and not p.get("message") and p.get("at", 0) < cutoff]
        if stale:
            self._pins = [p for p in self._pins if p not in stale]
            self._keep()

    # -- reading ----------------------------------------------------------

    def all(self) -> list[dict]:
        self._expire()
        out = []
        for pin in self._pins:
            if pin.get("self") and not pin.get("message"):
                continue
            item = dict(pin)
            if self._row(item.get("message", "")) is None:
                item["missing"] = True
            out.append(item)
        return out

    # -- changing ---------------------------------------------------------

    def add(self, session: str, message: str, *, by: str = "person",
            title: str = "", line: str = "", kind: str = "") -> dict:
        message = str(message or "").strip()
        if not message:
            raise ValueError("a message is needed")
        row = self._row(message)
        if row is None:
            raise LookupError("no such message")
        if by == "person" and (kind or row.get("kind")) == "ask":
            raise ValueError("an ask cannot be pinned")
        for pin in self._pins:
            if pin.get("message") != message or pin.get("self"):
                continue
            if title:
                pin["title"] = title[:80]
            if line:
                pin["line"] = line[:60]
            if kind:
                pin["kind"] = kind
            self._keep()
            self._announce("add", {"pin": pin})
            return pin
        if (sum(p.get("session") == session for p in self._pins) >= PER_SESSION
                or len(self._pins) >= TOTAL):
            raise OverflowError("the dock is full")
        fallback = describe(row)
        pin = {
            "id": uuid.uuid4().hex[:12], "session": session, "message": message,
            "order": max((p.get("order", 0) for p in self._pins), default=0) + 1,
            "created_at": time.time(), "by": by,
            "title": (title or fallback[0])[:80],
            "line": (line or fallback[1])[:60],
            "kind": kind or fallback[2],
        }
        self._pins.append(pin)
        self._keep()
        self._announce("add", {"pin": pin})
        return pin

    def remove(self, pin_id: str) -> bool:
        for i, pin in enumerate(self._pins):
            if pin.get("id") != pin_id:
                continue
            self._pins.pop(i)
            self._keep()
            self._announce("remove", {"id": pin_id})
            return True
        return False

    def drop_session(self, session: str) -> int:
        before = len(self._pins)
        self._pins = [p for p in self._pins if p.get("session") != session]
        dropped = before - len(self._pins)
        if dropped:
            self._keep()
            self._announce("remove", {"session": session})
        return dropped

    def prune(self) -> int:
        if self._sessions is None:
            return 0
        known = {row["id"] for row in self._sessions.listed()}
        before = len(self._pins)
        self._pins = [p for p in self._pins if p.get("session") in known]
        dropped = before - len(self._pins)
        if dropped:
            self._keep()
        return dropped

    # -- the model's side -------------------------------------------------

    def resolve(self, text: str) -> str | None:
        """``now``, ``latest``, an id, or a few words out of a message."""
        text = str(text or "").strip()
        if not text:
            return None
        if text == "now":
            return "now"
        if self._sessions is None:
            return None
        rows = self._sessions.read(self._sessions.active)
        if text == "latest":
            return rows[-1]["id"] if rows else None
        if _ID.fullmatch(text):
            return text if self._row(text) is not None else None
        needle = re.sub(r"\s+", " ", text.lower())
        if len(needle) < 4:
            return None
        for row in reversed(rows):
            hay = re.sub(r"\s+", " ", f"{row.get('text', '')} "
                         f"{(row.get('artifact') or {}).get('title', '')}".lower())
            if needle in hay:
                return row["id"]
        return None

    def claim_self(self, session: str) -> None:
        self._expire()
        self._pins = [p for p in self._pins
                      if not (p.get("self") and p.get("session") == session)]
        self._pins.append({"session": session, "message": "", "self": True,
                           "at": time.time()})
        self._keep()

    def bind_self(self, session: str, message: str) -> dict | None:
        row = self._row(message)
        if row is None:
            return None
        for pin in self._pins:
            if not (pin.get("self") and pin.get("session") == session and not pin.get("message")):
                continue
            fallback = describe(row)
            pin.pop("self", None)
            pin.pop("at", None)
            pin.update({
                "id": uuid.uuid4().hex[:12], "message": message,
                "order": max((p.get("order", 0) for p in self._pins), default=0) + 1,
                "created_at": time.time(), "by": "model",
                "title": fallback[0], "line": fallback[1], "kind": fallback[2],
            })
            self._keep()
            self._announce("add", {"pin": pin})
            return pin
        return None

    def describe(self, row: dict) -> tuple[str, str, str]:
        return describe(row)
