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
from urllib.parse import urlparse

from heare.api.canvas import _ID as _BLOCK
from heare.api.canvas import _text, clean
from heare.core.i18n import _

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


def _label(html: str) -> str:
    low = html.lower()
    if "<table" in low:
        return "table"
    if "<script" in low:
        return "game"
    return "card"


def _verb(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


_SHOW = _verb(
    "show",
    "Put a card in the chat — the heare page in the browser. Use it by "
    "yourself, without being asked, when something is better seen than heard "
    "(a list, steps, a table, numbers, code) or the person wants something to "
    "keep an eye on. Not for small talk or a one-line answer. The gist goes in "
    "`say`, the detail in the card. HTML only, no <style>; start with "
    "<h3>title</h3>. Helpers: class=\"big\", \"muted\", \"row\", "
    "\"meter\" with style=\"--v:0.42\", \"pill\". A live card carries "
    "one <script>; it can read heare.get('state'|'tasks').",
    {
        "html": {"type": "string", "description": "The card's HTML."},
        "title": {"type": "string", "description": "A short title for the card and the dock."},
        "say": {"type": "string", "description": "One short sentence to say aloud."},
        "width": {"type": "string", "enum": ["s", "w"],
                  "description": "s: the reading column (default); w: the whole panel."},
        "label": {"type": "string", "description": "Optional short kind, e.g. table, weather, game."},
    },
    ["html", "say"],
)
_SHOW_LINK = _verb(
    "show_link",
    "Show a web link as a card in the chat. Only http:// or https:// links.",
    {"url": {"type": "string"}, "title": {"type": "string"},
     "say": {"type": "string", "description": "One short sentence to say aloud."}},
    ["url", "say"],
)
_SHOW_MEDIA = _verb(
    "show_media",
    "Show a file that is in this conversation as a card: an image, an audio "
    "recording or a video.",
    {"file": {"type": "string", "description": "The file id from this conversation."},
     "kind": {"type": "string", "enum": ["image", "audio", "video"]},
     "title": {"type": "string"},
     "say": {"type": "string", "description": "One short sentence to say aloud."}},
    ["file", "kind", "say"],
)


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
        shown = [m for m in self._sessions.read(self._sessions.active)
                 if m["kind"] != "text" and m["status"] != "withdrawn"]
        if not shown:
            return ""
        lines = []
        for message in shown[-8:]:
            artifact = message.get("artifact") or {}
            label = artifact.get("label") or message["kind"]
            lines.append(f"- {message['id']} [{label}] {message['width']}: "
                         f"{message['text'][:120]}")
        return ("What the page shows, newest last (ids are for pins and width):\n"
                + "\n".join(lines))[:900]

    def verbs(self) -> list[tuple[dict, object]]:
        return [(_SHOW, self._show), (_SHOW_LINK, self._show_link),
                (_SHOW_MEDIA, self._show_media)]

    async def _show(self, arguments: dict) -> str:
        html = clean(str(arguments.get("html", "")))
        if not html.strip():
            return _("Nothing to show.")
        say = str(arguments.get("say", "")).strip()
        label = str(arguments.get("label", "")).strip() or _label(html)
        title = str(arguments.get("title", "")).strip() or _title(html) or label
        width = str(arguments.get("width", "")).strip()
        if width not in ("s", "w"):
            width = "w" if label in ("table", "weather", "game") else "s"
        self._sessions.add(self._sessions.active, "assistant", say or _text(html)[:400],
                           kind="html", html=html, width=width,
                           artifact={"label": label, "title": title,
                                     "live": bool(_LIVE.search(html))})
        return say or _("It is on the page.")

    async def _show_link(self, arguments: dict) -> str:
        url = str(arguments.get("url", "")).strip()
        if not re.match(r"^https?://", url, re.IGNORECASE):
            return _("That link cannot be shown.")
        title = str(arguments.get("title", "")).strip() or url
        say = str(arguments.get("say", "")).strip()
        self._sessions.add(self._sessions.active, "assistant", say or title,
                           kind="link", width="s",
                           artifact={"url": url, "title": title,
                                     "host": urlparse(url).netloc})
        return say or _("It is on the page.")

    async def _show_media(self, arguments: dict) -> str:
        file_id = str(arguments.get("file", ""))
        kind = str(arguments.get("kind", "")).strip()
        found = self._sessions.stored(self._sessions.active, file_id)
        if found is None or kind not in ("image", "audio", "video"):
            return _("That file is not in this conversation.")
        meta, _path = found
        title = str(arguments.get("title", "")).strip() or meta["name"]
        say = str(arguments.get("say", "")).strip()
        self._sessions.add(self._sessions.active, "assistant", say or title,
                           kind=kind, width="s" if kind == "audio" else "w",
                           artifact={"file": file_id, "name": meta["name"],
                                     "mime": meta.get("mime", ""), "title": title})
        return say or _("It is on the page.")

    async def say(self, pieces: AsyncIterator[str]) -> str:
        async for piece in pieces:
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
