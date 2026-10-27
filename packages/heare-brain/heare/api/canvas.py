"""The canvas: the assistant's second voice, for what is better seen.

Speaking is easy for a model because the reply is the speech. Drawing
was not: it was a job handed through two more models that wrote a whole
page each time, twenty seconds for a table. Here the model that talks
draws by itself, in the same turn, and changes only what it means to.

The canvas is one screen of named widgets laid out as cards, and they
stay: asking for a second thing adds a card beside the first. Three
verbs: put a widget (new, or in place of the one with that name), remove
one, clear all. The page supplies the styling, so everything on it looks
like one thing made by one hand, and a widget is small plain HTML.

A widget may be alive. One that carries a script is run by the page in
a sealed frame of its own, where it can refresh itself, ask a public
service for data, and ask the assistant about this machine through a
small bridge (``heare.get``) — and nothing else. What is on the canvas is told to the model in every
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
# What every turn carries is the short outline; the long one, and the
# stylesheet, come by ``canvas_view`` when the model is about to change
# them. Up to 1800 characters of CSS were in every prompt before (27 October).
TURN_CHARS = 120
TURN_TOTAL = 900
_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,40}")
_PAIRED = re.compile(r"<\s*(iframe|object|embed)\b.*?</\s*\1\s*>", re.IGNORECASE | re.DOTALL)
_LONE = re.compile(r"</?\s*(iframe|object|embed|link|meta|base)\b[^>]*>?", re.IGNORECASE)
SIZES = ("s", "m", "l")
STYLE_CHARS = 6000
_IMPORT = re.compile(r"@import[^;]*;?", re.IGNORECASE)
_HANDLER = re.compile(r"\son[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
_JS_URL = re.compile(r"javascript\s*:", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def clean(html: str) -> str:
    """Frames, external sheets, inline handlers and script URLs are taken
    out. A <script> stays: the page never runs it in itself, only inside
    a sealed frame that can reach nothing of the person's."""
    html = _LONE.sub("", _PAIRED.sub("", html))
    html = _HANDLER.sub("", html)
    return _JS_URL.sub("", html)


def _text(html: str) -> str:
    html = re.sub(r"<\s*(script|style)\b.*?</\s*\1\s*>", " ", html, flags=re.I | re.S)
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
        self.sizes: dict[str, str] = {}
        # The canvas's own stylesheet: how it is laid out and how it
        # looks, beyond the default. Content, order and this are the
        # whole of what a screen is, so nothing about it is out of reach.
        self.css = ""
        try:
            saved = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                self.css = str(saved.get("css", ""))
                saved = saved.get("blocks", [])
            for row in saved:
                self.blocks[str(row[0])] = str(row[1])
                if len(row) > 2 and row[2] in SIZES:
                    self.sizes[str(row[0])] = row[2]
        except (OSError, ValueError, TypeError, IndexError):
            pass

    def _keep(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({
                    "blocks": [[k, v, self.sizes.get(k, "s")]
                               for k, v in self.blocks.items()],
                    "css": self.css,
                }, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass  # a drawing that cannot be saved is still drawn

    def view(self) -> dict:
        return {
            "blocks": [{"id": k, "html": v, "size": self.sizes.get(k, "s")}
                       for k, v in self.blocks.items()],
            "css": self.css,
        }

    # -- what the model does -------------------------------------------

    def put(self, block: str, html: str, after: str = "", size: str = "") -> None:
        block = block.strip().lower()
        if not _ID.fullmatch(block):
            raise ValueError("a block's name is lowercase latin letters, digits, dashes")
        html = clean(html)[:BLOCK_CHARS]
        if size in SIZES:
            self.sizes[block] = size
        elif block not in self.sizes:
            # Unsaid: a table or a long text wants the width, a number does not.
            self.sizes[block] = "m" if "<table" in html or len(_text(html)) > 400 else "s"
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
                   size=self.sizes[block], order=list(self.blocks))

    def remove(self, block: str) -> bool:
        if self.blocks.pop(block.strip().lower(), None) is None:
            return False
        self.sizes.pop(block.strip().lower(), None)
        self._keep()
        self._emit("canvas", op="remove", id=block.strip().lower())
        return True

    def style(self, css: str) -> None:
        """Replace the canvas's own stylesheet. Nothing is fetched by it:
        an @import is taken out, and what it can do is change how the
        canvas looks."""
        self.css = _JS_URL.sub("", _IMPORT.sub("", css)).replace("</style", "")[:STYLE_CHARS]
        self._keep()
        self._emit("canvas", op="style", css=self.css)

    def arrange(self, order: list[str], sizes: dict[str, str] | None = None) -> None:
        """Change where the widgets stand and how wide they are, without
        touching what is in them. Ids left out keep their place after
        the ones named."""
        named = [k for k in dict.fromkeys(str(i).strip().lower() for i in order)
                 if k in self.blocks]
        rest = [k for k in self.blocks if k not in named]
        self.blocks = {k: self.blocks[k] for k in (*named, *rest)}
        for key, size in (sizes or {}).items():
            if key in self.blocks and size in SIZES:
                self.sizes[key] = size
        self._keep()
        self._emit("canvas", op="arrange", order=list(self.blocks),
                   sizes={k: self.sizes.get(k, "s") for k in self.blocks})

    def clear(self) -> None:
        self.blocks, self.sizes, self.css = {}, {}, ""
        self._keep()
        self._emit("canvas", op="clear")

    async def _put(self, arguments: dict) -> str:
        try:
            self.put(str(arguments.get("id", "")), str(arguments.get("html", "")),
                     str(arguments.get("after", "") or ""),
                     str(arguments.get("size", "") or ""))
        except ValueError:
            return ""
        # Said only when the model drew without a word: silence after a
        # question is the worst answer, even with the answer on the screen.
        return str(arguments.get("say", "")).strip() or "Глянь на екран."

    async def _remove(self, arguments: dict) -> str:
        self.remove(str(arguments.get("id", "")))
        return "Прибрав."

    async def _style(self, arguments: dict) -> str:
        self.style(str(arguments.get("css", "")))
        return str(arguments.get("say", "")).strip() or "Змінив вигляд."

    async def _arrange(self, arguments: dict) -> str:
        order = arguments.get("order") or []
        sizes = arguments.get("sizes") or {}
        if not isinstance(order, list) or not isinstance(sizes, dict):
            return ""
        self.arrange(order, sizes)
        return "Переставив."

    async def _view(self, arguments: dict) -> str:
        return self.full()

    async def _clear(self, arguments: dict) -> str:
        self.clear()
        return "Очистив екран."

    def verbs(self) -> list[tuple[dict, object]]:
        return [(_PUT, self._put), (_STYLE, self._style), (_ARRANGE, self._arrange),
                (_REMOVE, self._remove), (_CLEAR, self._clear), (_VIEW, self._view)]

    def outline(self, each: int = OUTLINE_CHARS, total: int = OUTLINE_TOTAL) -> str:
        """Names and first words. Enough to change the right block; never
        the markup itself."""
        lines = [
            f"- {key} [{self.sizes.get(key, 's')}]"
            f"{' (живий)' if '<script' in html.lower() else ''}: "
            f"{_text(html)[:each]}" for key, html in self.blocks.items()
        ]
        said = "\n".join(lines)
        return said if len(said) <= total else said[:total] + " …"

    def context(self) -> str:
        """Every turn: what is drawn, short. Not «the screen» — that word is
        the Mac's and the panel's too."""
        if not self.blocks:
            return "The canvas (the heare page in the browser) is empty."
        own = ("\nThe canvas has a stylesheet of its own; look at it with "
               "canvas_view before changing the look." if self.css else "")
        return ("The canvas (the heare page in the browser) already shows, left to "
                "right, width in brackets; do not draw the same again, and look at "
                "a widget with canvas_view before adding to it:\n"
                + self.outline(TURN_CHARS, TURN_TOTAL) + own)

    def full(self) -> str:
        """On asking: the long outline and the stylesheet."""
        if not self.blocks and not self.css:
            return "The canvas is empty, the style is the default."
        own = (f"\nIts own stylesheet (canvas_style replaces it whole):\n{self.css}"
               if self.css else "\nThe style is the default.")
        return (self.outline() or "No widgets.") + own

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
    "Put a widget on the canvas — the heare page in the browser. "
    "Use it by yourself, without being asked, whenever something is better "
    "seen than heard (a list, steps, a table, numbers, code) or the person "
    "wants something to keep an eye on (weather, the processor, a timer). "
    "Not for small talk or a one-line answer. The gist goes in `say`, the "
    "detail on the canvas. Widgets stay side by side: a new id adds a card, "
    "an existing id replaces that card in place — to change one thing, put "
    "only that widget again. Keep each small.\n"
    "HTML only, no <style>: the canvas styles everything alike. Start with "
    "<h3>title</h3>. Helpers: class=\"big\" a large value, \"muted\" small "
    "grey text, \"row\" a label–value line (two spans), \"meter\" with "
    "style=\"--v:0.42\" a bar, \"pill\" a tag. <button data-say=\"…\"> says "
    "that to you.\n"
    "A LIVE widget carries one <script> that fills elements by id and "
    "refreshes itself with setInterval. In it: `await heare.get('system')` "
    "-> {cpu:{percent,load,cores}, memory:{percent,used_gb,total_gb}, "
    "battery:{percent,state}, disk:{free_gb,total_gb}, top:[{name,cpu,mb}]}; "
    "`heare.get('state')`, `heare.get('tasks')`; and fetch() of public JSON "
    "APIs, e.g. weather: https://geocoding-api.open-meteo.com/v1/search?"
    "name=CITY&count=1 then https://api.open-meteo.com/v1/forecast?latitude="
    "…&longitude=…&current=temperature_2m,apparent_temperature,"
    "weather_code,wind_speed_10m. Never invent live numbers — fetch them.",
    {
        "id": {"type": "string",
               "description": "Short latin name, e.g. «weather-burshtyn», "
                              "«cpu». Reuse it to change the widget."},
        "html": {"type": "string", "description": "The widget's HTML."},
        "say": {"type": "string",
                "description": "One short sentence to say aloud, in the "
                               "person's language: the answer in brief, not "
                               "«look at the screen»."},
        "size": {"type": "string", "enum": ["s", "m", "l"],
                 "description": "s: a small card (default), m: double "
                                "width, l: the full row."},
    },
    ["id", "html", "say"],
)
_STYLE = _verb(
    "canvas_style",
    "Change how the canvas looks and how it is laid out — anything about "
    "appearance that is not the content of one widget: columns and rows, "
    "where things stand and how big they are, grouping, colours and theme, "
    "fonts, backgrounds, rounded or round widgets, animation. Plain CSS; "
    "this REPLACES the canvas's own stylesheet, so include what should "
    "stay. Selectors: `#live` is the grid of widgets (display:grid — set "
    "grid-template-columns, gap, grid-auto-rows…); `.blk` is every widget "
    "card; `#blk-<id>` is one widget (grid-column / grid-row to place it, "
    "any look); `.w h3`, `.w .big`, `.w .row`, `.w table` are what is "
    "inside. Theme by setting variables on `#stage`: --bg --card --card-2 "
    "--line --ink --soft --accent. @keyframes are allowed. An empty css "
    "returns the default look.",
    {
        "css": {"type": "string", "description": "The whole stylesheet."},
        "say": {"type": "string",
                "description": "One short sentence to say aloud about it."},
    },
    ["css", "say"],
)
_ARRANGE = _verb(
    "canvas_arrange",
    "Move widgets around or resize them without redrawing them: when the "
    "person asks to sort, group, reorder, put something first, or make a "
    "widget wider or smaller. The canvas fills left to right, top to "
    "bottom, in this order.",
    {
        "order": {"type": "array", "items": {"type": "string"},
                  "description": "Widget ids in the new order. Ones you "
                                 "leave out follow in their current order."},
        "sizes": {"type": "object",
                  "description": "Optional: id -> \"s\", \"m\" or \"l\" for "
                                 "widgets whose width should change.",
                  "additionalProperties": {"type": "string", "enum": ["s", "m", "l"]}},
    },
    ["order"],
)
_REMOVE = _verb(
    "canvas_remove", "Take one widget off the canvas.",
    {"id": {"type": "string", "description": "The widget's id."}}, ["id"],
)
_VIEW = _verb(
    "canvas_view",
    "Look at the canvas closely: each widget's words at length and its own "
    "stylesheet. Before adding to a widget or changing the look.", {}, [],
)
_CLEAR = _verb(
    "canvas_clear",
    "Wipe the canvas. Only when the person asks, or the subject has changed "
    "and nothing drawn is of use any more.", {}, [],
)
