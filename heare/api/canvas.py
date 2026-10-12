"""The canvas: the assistant's second voice, for what is better seen.

Speaking is easy for a model because the reply is the speech. Drawing
was not: it was a job handed through two more models that wrote a whole
page each time, twenty seconds for a table. Here the model that talks
draws by itself, in the same turn, and changes only what it means to.

The canvas is a column of named blocks. Three verbs: put a block (new,
or in place of the one with that name), remove one, clear all. The page
supplies the styling, so a block is plain HTML — a heading, a table, a
list — and small. What is on the canvas is told to the model in every
prompt as an outline, not as markup: the old app put the whole display
into the prompt and the model drowned in it.

A button with ``data-say="…"`` says that to the assistant when pressed,
so what is drawn can answer back.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import AsyncIterator, Callable

# A block is a fragment, not a page. Past this it should have been
# several blocks, or a screen.
BLOCK_CHARS = 6000
# Per block and in all. Too short and the model forgets what it drew — it
# redrew a table to add a row that was already there; too long and the
# prompt drowns, as the old app's did with the whole display in it.
OUTLINE_CHARS = 400
OUTLINE_TOTAL = 2400
_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,40}")
_SCRIPT = re.compile(r"<\s*(script|iframe|object|embed|link|meta)\b.*?(</\s*\1\s*>|$)",
                     re.IGNORECASE | re.DOTALL)
_HANDLER = re.compile(r"\son[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
_JS_URL = re.compile(r"javascript\s*:", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def clean(html: str) -> str:
    """A block may hold markup, not code: what runs on the page is the
    page's own. Scripts, frames and inline handlers are taken out."""
    html = _SCRIPT.sub("", html)
    html = _HANDLER.sub("", html)
    return _JS_URL.sub("", html)


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub(" ", html)).strip()


class Canvas:
    name = "canvas"
    abilities: frozenset[str] = frozenset()
    speaking = False

    def __init__(self, path: Path, emit: Callable[..., object]) -> None:
        """path: where the drawing is kept, so it is still there after a
        restart. emit: the core's event, so every open page changes at once."""
        self._path = Path(path)
        self._emit = emit
        self.blocks: dict[str, str] = {}
        try:
            saved = json.loads(self._path.read_text(encoding="utf-8"))
            self.blocks = {str(k): str(v) for k, v in saved}
        except (OSError, ValueError, TypeError):
            pass

    def _keep(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(list(self.blocks.items()), ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass  # a drawing that cannot be saved is still drawn

    def view(self) -> list[dict]:
        return [{"id": k, "html": v} for k, v in self.blocks.items()]

    # -- what the model does -------------------------------------------

    def put(self, block: str, html: str, after: str = "") -> None:
        block = block.strip().lower()
        if not _ID.fullmatch(block):
            raise ValueError("a block's name is lowercase latin letters, digits, dashes")
        html = clean(html)[:BLOCK_CHARS]
        if block in self.blocks or after not in self.blocks:
            self.blocks[block] = html  # in place, or at the end
        else:
            items = []
            for key, value in self.blocks.items():
                items.append((key, value))
                if key == after:
                    items.append((block, html))
            self.blocks = dict(items)
        self._keep()
        self._emit("canvas", op="put", id=block, html=html,
                   order=list(self.blocks))

    def remove(self, block: str) -> bool:
        if self.blocks.pop(block.strip().lower(), None) is None:
            return False
        self._keep()
        self._emit("canvas", op="remove", id=block.strip().lower())
        return True

    def clear(self) -> None:
        self.blocks = {}
        self._keep()
        self._emit("canvas", op="clear")

    async def _put(self, arguments: dict) -> str:
        try:
            self.put(str(arguments.get("id", "")), str(arguments.get("html", "")),
                     str(arguments.get("after", "") or ""))
        except ValueError:
            return ""
        # Said only when the model drew without a word: silence after a
        # question is the worst answer, even with the answer on the screen.
        return str(arguments.get("say", "")).strip() or "Глянь на екран."

    async def _remove(self, arguments: dict) -> str:
        self.remove(str(arguments.get("id", "")))
        return "Прибрала."

    async def _clear(self, arguments: dict) -> str:
        self.clear()
        return "Очистила екран."

    def verbs(self) -> list[tuple[dict, object]]:
        return [(_PUT, self._put), (_REMOVE, self._remove), (_CLEAR, self._clear)]

    def context(self) -> str:
        """An outline: names and first words. Enough to change the right
        block; never the markup itself."""
        if not self.blocks:
            return "Канвас (екран перед людиною) зараз порожній."
        lines = [
            f"- {key}: {_text(html)[:OUTLINE_CHARS]}" for key, html in self.blocks.items()
        ]
        outline = "\n".join(lines)
        if len(outline) > OUTLINE_TOTAL:
            outline = outline[:OUTLINE_TOTAL] + " …"
        return ("На канвасі (екран перед людиною) зараз такі блоки, згори донизу. "
                "Це вже намальовано — не малюй те саме вдруге:\n" + outline)

    # -- as an output: a reply sent here is written, not spoken --------

    async def say(self, pieces: AsyncIterator[str]) -> str:
        text = "".join([piece async for piece in pieces]).strip()
        if text:
            self.put("note", f"<p>{_escape(text)}</p>")
        return "done"

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def stop(self) -> None: ...


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _verb(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }}


_PUT = _verb(
    "canvas_put",
    "Draw on the canvas — the screen in front of the person. Use it by "
    "yourself, without being asked, whenever something is better seen than "
    "heard: a list, steps, a table, a comparison, numbers, code, an address, "
    "anything they may want to look at again. Not for small talk or a "
    "one-line answer. The gist goes in `say` — one short spoken sentence "
    "that answers the person — and the detail goes on the canvas; do not "
    "read the detail out. "
    "One call puts ONE block. A block with a new id is added at the end; a "
    "block with an id that exists is replaced in place. To change part of "
    "what is drawn, put only that block again — never redraw the rest. Keep "
    "blocks small: a heading is a block, a table is a block. Plain HTML "
    "only (h2, h3, p, ul, ol, table, pre, code, strong, em, a, button) — "
    "no style, no script: the canvas styles it. A <button data-say=\"…\"> "
    "says that text to you when pressed.",
    {
        "id": {"type": "string",
               "description": "Short latin name of the block, e.g. «plan», "
                              "«prices-table». Reuse it to change the block."},
        "html": {"type": "string", "description": "The block's HTML."},
        "say": {"type": "string",
                "description": "One short sentence to say aloud, in the "
                               "person's language: the answer in brief, not "
                               "«look at the screen»."},
        "after": {"type": "string",
                  "description": "Optional: id of the block to put a new one after."},
    },
    ["id", "html", "say"],
)
_REMOVE = _verb(
    "canvas_remove", "Take one block off the canvas.",
    {"id": {"type": "string", "description": "The block's id."}}, ["id"],
)
_CLEAR = _verb(
    "canvas_clear",
    "Wipe the canvas. Only when the person asks, or the subject has changed "
    "and nothing drawn is of use any more.", {}, [],
)
