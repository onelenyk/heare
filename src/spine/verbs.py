"""The voice's verbs, sorted by what they are about.

Every verb belongs to one of two categories, and the category decides
where it lives:

    CONVERSATION  about the talk itself — its past (``recall``), its
                  future (``remind``), and forgetting what the room said
                  (``forget``). Only the voice holds the conversation, and
                  these must land inside the turn that asked.
    WORLD         anything outside the talk: the screen, the canvas
                  panel, files, the browser, mail, the shell. One door —
                  ``delegate`` — and the hands behind it.

A third kind is not a verb at all. «Стоп», «режим інтерв'ю», «кінець» are
commands: they are caught by phrase in code (the barge-in judge, the role
triggers), because a command has to work every time and the model should
not be asked to guess whether it was one.

How the list grew to nine is the reason for the rule. Each time the
hands were slow or blind, the voice was given its own window onto the
world — the screen, the panel, a notebook — until it was choosing among
nine schemas, ~1,300 prompt tokens a turn. A slow door is fixed in the
hands, not by cutting a new one.

To add a verb: ask first whether it is about the conversation or about
the world. About the world, it is a tool for the hands
(``src/agent/tools/system.py``), not a verb. About the conversation, it
is a method below decorated with ``@verb``; nothing else needs touching.

A verb's return value is spoken verbatim (``SpineLoop._run_tool``), so
every one returns a finished, short Ukrainian sentence and never raises.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from src.spine import search
from src.spine.hands import HandsFactory, _default_hands_factory

logger = logging.getLogger("heare.spine.tools")


class Category(StrEnum):
    CONVERSATION = "conversation"
    WORLD = "world"


@dataclass(frozen=True)
class Verb:
    """One verb: its name, what it is about, and the schema the model sees.

    ``offered`` decides, per toolbox, whether the model is shown it at all.
    A verb that cannot do anything here is not offered: a tool the model
    can see is a promise.
    """

    name: str
    category: Category
    description: str
    properties: dict
    required: tuple[str, ...]
    run: Callable[[Any, dict], Awaitable[str]]
    offered: Callable[[Any], bool] | None = None

    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.properties,
                    "required": list(self.required),
                },
            },
        }


def verb(
    category: Category,
    description: str,
    properties: dict | None = None,
    *,
    required: Sequence[str] = (),
    offered: Callable[[Any], bool] | None = None,
):
    """Make a ``VoiceToolbox`` method a verb. The method's name is the verb's."""

    def wrap(fn: Callable[[Any, dict], Awaitable[str]]):
        fn.__verb__ = Verb(
            fn.__name__,
            category,
            description,
            properties or {},
            tuple(required),
            fn,
            offered,
        )
        return fn

    return wrap


def _minutes_word(n: int) -> str:
    """«хвилину / хвилини / хвилин» — the rule, not the first three
    cases: 21 хвилину, 22 хвилини, 25 хвилин, 111 хвилин."""
    if n % 10 == 1 and n % 100 != 11:
        return "хвилину"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "хвилини"
    return "хвилин"


class VoiceToolbox:
    """The voice's verbs. The list is ``VERBS``, below the class."""

    def __init__(
        self,
        settings: Any,
        deliver: Callable[[str], Awaitable[None]],
        *,
        hands_factory: HandsFactory | None = None,
        persist: Any = None,
        names: Sequence[str] = (),
        notice: Callable[..., Awaitable[None]] | None = None,
        overheard: bool = False,
    ) -> None:
        """deliver: async fn; the hands' results come back through it.
        persist: the transcript store — None means nothing is written
        down, so there is nothing to recall or forget.
        names: the assistant's own name in every spelling Whisper
        produces. A search query keeps it far more often than not, and in
        the transcripts it is the one word that matches almost everything.
        notice: async fn for reminders; absent, `remind` says they are off.
        overheard: whether the room is being kept (``hear_all``). Only
        then is there anything for `forget` to erase.
        hands_factory: a seam for tests; builds the hands from settings.
        """
        self._settings = settings
        self._persist = persist
        self._names = tuple(names)
        self._notice = notice
        self._overheard = overheard
        self._hands = (hands_factory or _default_hands_factory)(settings)
        self._hands.set_delivery(deliver)
        self._verbs = {v.name: v for v in VERBS if v.offered is None or v.offered(self)}

    @property
    def schemas(self) -> list[dict]:
        """The schemas the model sees this turn."""
        return [v.schema for v in self._verbs.values()]

    async def execute(self, name: str, arguments: dict) -> str:
        """Run one verb. Never raises into the voice loop."""
        found = self._verbs.get(name)
        if found is None:
            return "Такої дії я не знаю."
        try:
            return await found.run(self, arguments)
        except Exception:
            logger.exception("[SPINE TOOLS] %s failed: %.200s", name, arguments)
            return "Не вийшло. Спробуй ще раз."

    def cancel_all(self) -> int:
        """Stop everything the hands have in flight."""
        return self._hands.cancel_all()

    # -- the world: one door -------------------------------------------

    @verb(
        Category.WORLD,
        "Your hands in the world. Everything outside this conversation "
        "goes through here: what is on the person's screen or in their "
        "browser, the canvas panel (draw on it, read it, clear it), "
        "files, the shell, the web, mail, settings. You cannot see or "
        "touch any of it yourself — only through this, so never say you "
        "cannot see something before you have asked. Returns at once: say "
        "in one short sentence that you are on it, then stop. The answer "
        "arrives later as its own message.",
        {
            "task": {
                "type": "string",
                "description": (
                    "What to do, in full. The hands see the last few turns "
                    "of this conversation, but name the thing itself."
                ),
            }
        },
        required=("task",),
    )
    async def delegate(self, arguments: dict) -> str:
        task = str(arguments.get("task", "")).strip()
        if not task:
            return "Скажи, що саме зробити."
        # Never awaited: the result comes back later through `deliver`.
        self._hands.start(task)
        return "Прийнято, роблю."

    # -- the conversation --------------------------------------------------

    @verb(
        Category.CONVERSATION,
        "Search what was actually said in your past conversations with "
        "this person — whenever they ask what they told you, what you "
        "agreed, or when something came up.",
        {
            "query": {
                "type": "string",
                "description": (
                    "The words to look for — the subject, not the whole question."
                ),
            },
            "when": {
                "type": "string",
                "description": (
                    "Their own words about when, if they said any: «вчора», "
                    "«минулого тижня», «у вівторок», «три дні тому». Leave "
                    "out if they did not."
                ),
            },
            "room": {
                "type": "boolean",
                "description": (
                    "Include speech overheard in the room rather than said "
                    "to you. Only when they explicitly ask about what was "
                    "said around you — never as a second try."
                ),
            },
        },
        required=("query",),
        offered=lambda box: box._persist is not None,
    )
    async def recall(self, arguments: dict) -> str:
        """The sentence is finished here, not handed back to the model:
        it is spoken as returned, so it obeys the voice rules and names
        when. Off the event loop — synchronous SQLite over thousands of
        rows, and the loop it would block carries the audio."""
        query = str(arguments.get("query", "")).strip()
        if not query:
            return "Що саме згадати?"
        now = datetime.now()
        fragments = await asyncio.to_thread(
            search.find,
            self._persist,
            query,
            now=now,
            when=str(arguments.get("when", "") or ""),
            room=bool(arguments.get("room", False)),
            names=self._names,
        )
        logger.info("search: %r -> %d fragments", query, len(fragments))
        return search.spoken(fragments, now=now)

    @verb(
        Category.CONVERSATION,
        "Set a reminder: you will say it when the time comes and the "
        "person is present and quiet.",
        {
            "minutes": {
                "type": "integer",
                "description": (
                    "How many minutes until the reminder, 1 to 1440 (24 hours)."
                ),
            },
            "text": {"type": "string", "description": "What to remind about."},
        },
        required=("minutes", "text"),
    )
    async def remind(self, arguments: dict) -> str:
        """Stored as an intent with a due time; the engine speaks it."""
        if self._notice is None:
            return "Не можу нагадати — нагадування вимкнені."
        try:
            minutes = int(arguments.get("minutes", 0))
        except (TypeError, ValueError):
            return "Скажи, за скільки хвилин нагадати."
        if minutes <= 0 or minutes > 24 * 60:
            return "Нагадування має бути від 1 хвилини до 24 годин."
        text = str(arguments.get("text", "")).strip()
        if not text:
            return "Скажи, про що нагадати."
        due_ts = time.time() + minutes * 60
        held = await self._notice(
            kind="reminder",
            text=text,
            origin="user",
            urgency=0.9,
            due_ts=due_ts,
            dedupe_key=f"remind:{text}:{due_ts:.0f}",
        )
        if held is False:
            # The engine keeps a reminder between turns; without one the
            # promise would be made and never kept.
            return "Не можу нагадати — нагадування вимкнені."
        return f"Добре, нагадаю за {minutes} {_minutes_word(minutes)}."

    @verb(
        Category.CONVERSATION,
        "Erase what was said in the room recently but not to you — when "
        "the person asks you to forget the last hour, or says that what "
        "was just said should not be kept. Your own conversations with "
        "them are never touched.",
        {
            "minutes": {
                "type": "integer",
                "description": (
                    "How far back to erase, in minutes. Default 60 if they did not say."
                ),
            }
        },
        offered=lambda box: box._persist is not None and box._overheard,
    )
    async def forget(self, arguments: dict) -> str:
        """The counterweight to keeping the room at all: «забудь» said out
        loud has to be true, without opening a database. Deliberately
        narrow — only what was overheard, never a conversation."""
        try:
            minutes = int(arguments.get("minutes") or 60)
        except (TypeError, ValueError):
            minutes = 60
        minutes = max(1, min(minutes, 24 * 60))
        cutoff = time.time() - minutes * 60
        gone = await asyncio.to_thread(
            self._persist.forget_overheard_since, after_ts=cutoff
        )
        if not gone:
            return "Там і не було чого забувати."
        return f"Забула. {gone} рядків за останні {minutes} хвилин."


# The fixed list, in the order the model sees it. Built from the class so
# a verb cannot be written and forgotten here, or listed and not written.
VERBS: tuple[Verb, ...] = tuple(
    fn.__verb__ for fn in vars(VoiceToolbox).values() if hasattr(fn, "__verb__")
)
