"""The page as a feed of messages.

A message is a session turn (``sessions.py``). The old canvas is a name for
cards that are now ``kind: "html"`` messages: the timeline migrates the old
``canvas.json`` once, keeps the ``/api/canvas*`` shims alive for one release,
and is the Surface the model will draw on (the verbs arrive next).
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import AsyncIterator

from heare.api.canvas import _ID as _BLOCK
from heare.api.canvas import _text, clean

logger = logging.getLogger("heare.api.timeline")

_TITLE = re.compile(r"<\s*h[23][^>]*>(.*?)</\s*h[23]\s*>", re.IGNORECASE | re.DOTALL)
_LIVE = re.compile(r"<\s*script\b", re.IGNORECASE)
_MARK = "canvas.migrated.json"
_WIDE = ("m", "l")


def _title(html: str) -> str:
    found = _TITLE.search(html)
    return clean("".join(found.groups())).strip() if found else ""


def _artifact(html: str, block: str, label: str = "canvas") -> dict:
    return {"label": label, "block": block, "title": _title(html) or block,
            "live": bool(_LIVE.search(html))}


class Timeline:
    """The page's feed, and the one place that still knows the old canvas.

    It is a Surface (for the verbs that come next) and an Output (the page is
    a channel, but Screen still speaks the replies; ``say`` here is quiet)."""

    name = "page"
    abilities: frozenset[str] = frozenset()
    speaking = False

    def __init__(self, sessions, emit=None, canvas_file: Path | str | None = None) -> None:
        self._sessions = sessions
        self._emit = emit
        self._canvas_file = Path(canvas_file) if canvas_file is not None else None

    # -- the feed ---------------------------------------------------------

    def view(self) -> list[dict]:
        return self._sessions.read(self._sessions.active)

    def post(self, role: str, text: str, **fields: object) -> dict:
        return self._sessions.add(self._sessions.active, role, text, **fields)

    def patch(self, n: int, **fields: object) -> dict | None:
        return self._sessions.patch(self._sessions.active, n, **fields)

    def withdraw(self, n: int) -> dict | None:
        return self._sessions.patch(self._sessions.active, n, status="withdrawn")

    # -- the Surface and the Output --------------------------------------

    def context(self) -> str:
        return ""

    def verbs(self) -> list[tuple[dict, object]]:
        return []

    async def say(self, pieces: AsyncIterator[str]) -> str:
        async for _ in pieces:
            pass
        return "done"

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def stop(self) -> None: ...

    # -- the old canvas, as a view over messages -------------------------

    def _html(self) -> list[tuple[int, dict]]:
        return [(n, m) for n, m in enumerate(self._sessions.read(self._sessions.active))
                if m.get("kind") == "html" and m.get("status") != "withdrawn"]

    def canvas_view(self) -> dict:
        blocks = []
        for _n, message in self._html()[-30:]:
            artifact = message.get("artifact") or {}
            blocks.append({
                "id": str(artifact.get("block") or message["id"]),
                "html": message.get("html", ""),
                "size": "l" if message.get("width") == "w" else "s",
            })
        css = ""
        if self._canvas_file is not None:
            marker = self._canvas_file.with_name(_MARK)
            try:
                css = str(json.loads(marker.read_text(encoding="utf-8")).get("css", ""))
            except (OSError, ValueError):
                pass
        return {"blocks": blocks, "css": css}

    def canvas_put(self, block: str, html: str) -> dict:
        if not _BLOCK.fullmatch(str(block)):
            raise ValueError("a block's name is lowercase latin letters, digits, dashes")
        clean_html = clean(str(html))
        if not clean_html.strip():
            raise ValueError("an empty block")
        text = _text(clean_html)[:400]
        artifact = _artifact(clean_html, block)
        for n, message in self._html():
            if (message.get("artifact") or {}).get("block") != block:
                continue
            self._sessions.patch(self._sessions.active, n, html=clean_html,
                                 text=text, status="done", artifact=artifact)
            return self.canvas_view()
        self._sessions.add(self._sessions.active, "assistant", text, kind="html",
                           html=clean_html, artifact=artifact, width="s")
        return self.canvas_view()

    def canvas_remove(self, block: str) -> bool:
        for n, message in self._html():
            if (message.get("artifact") or {}).get("block") != block:
                continue
            self._sessions.patch(self._sessions.active, n, status="withdrawn")
            return True
        return False

    # -- the migration ----------------------------------------------------

    def migrate_legacy(self) -> int:
        """The old canvas becomes messages in the active session, once.

        The marker beside ``canvas.json`` is the idempotency: the old file is
        never rewritten or deleted, so a rollback leaves it alive."""
        if self._canvas_file is None or not self._canvas_file.is_file():
            return 0
        marker = self._canvas_file.with_name(_MARK)
        if marker.is_file():
            return 0
        try:
            data = json.loads(self._canvas_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        if not isinstance(data, dict):
            return 0
        rows = data.get("blocks", [])
        css = str(data.get("css", ""))
        session = self._sessions.active
        if not session:
            return 0
        try:
            mtime = self._canvas_file.stat().st_mtime
        except OSError:
            mtime = time.time()
        made = []
        for i, row in enumerate(rows):
            try:
                block, html = str(row[0]), str(row[1])
                size = row[2] if len(row) > 2 else "s"
            except (TypeError, IndexError, KeyError):
                continue
            if not _BLOCK.fullmatch(block) or not html.strip():
                continue
            clean_html = clean(html)
            artifact = _artifact(clean_html, block)
            message = self._sessions.add(
                session, "assistant", _text(clean_html)[:400], kind="html",
                html=clean_html, artifact=artifact,
                width="w" if size in _WIDE else "s", at=mtime + i * 0.001)
            made.append({"block": block, "id": message["id"]})
        try:
            marker.write_text(json.dumps({
                "v": 1, "at": time.time(), "session_id": session,
                "blocks": made, "css": css,
            }, ensure_ascii=False), encoding="utf-8")
        except OSError:
            logger.exception("the canvas marker could not be written")
        return len(made)
