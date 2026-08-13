"""VoiceToolbox — the voice agent's three verbs: delegate, remember, recall.

Mirrors the daemon's voice/hands split (see ``src/agent/hands.py`` and the
``VOICE_TOOLS`` set in ``src/agent/tools/system.py``): the voice model,
under a latency budget, only ever chooses among three schemas. Everything
heavier — files, the shell, the web — goes to ``Hands``, which has no
deadline and is not in the speaking path.

This module deliberately does not import ``src.agent.tools.system`` (it
drags in Pipecat and the rest of the sixty-tool surface). The schema
SHAPES below are copied in spirit, not by reference.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from src.agent.hands import Hands
from src.memory.base import MemoryEntry, MemoryType

logger = logging.getLogger("heare.spine.tools")

# A constructor seam: tests inject a fake in place of a real Hands
# instance without touching this module's import graph. Signature matches
# ``Hands.__init__``'s one required positional argument.
HandsFactory = Callable[[Any], Any]


def _default_hands_factory(settings: Any) -> Any:
    return Hands(settings)


SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "delegate",
            "description": (
                "Hand a task to your worker: anything needing files, the "
                "shell, the web, settings, the browser, or more than a "
                "moment's thought. Returns at once — say in one short "
                "sentence that you are on it, then stop. The answer "
                "arrives later as its own message."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "What to do, in full. The worker cannot see "
                            "this conversation, so name everything it "
                            "needs."
                        ),
                    }
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Store a fact in persistent memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["fact", "preference", "decision", "event"],
                        "description": "Type of memory to store.",
                    },
                    "content": {
                        "type": "string",
                        "description": "What to remember. A short sentence.",
                    },
                },
                "required": ["type", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "Search your persistent memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for in your memories.",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


class VoiceToolbox:
    """The voice agent's three verbs. Anything heavier goes to Hands."""

    def __init__(
        self,
        settings: Any,
        memory: Any,
        deliver: Callable[[str], Awaitable[None]],
        *,
        hands_factory: HandsFactory | None = None,
    ) -> None:
        """memory: an initialized SQLiteBackend (or compatible).
        deliver: async fn; Hands results are delivered through it
        (wired here via ``hands.set_delivery(deliver)``).

        hands_factory: optional seam for tests — a callable taking
        ``settings`` and returning a Hands-compatible object (must expose
        ``set_delivery``, ``start``, ``cancel_all``). Defaults to building
        a real ``Hands(settings)``. Hands is instantiated here, lazily,
        rather than at import time, so importing this module stays cheap.
        """
        self._settings = settings
        self._memory = memory
        self._deliver = deliver
        factory = hands_factory or _default_hands_factory
        self._hands = factory(settings)
        self._hands.set_delivery(deliver)

    @property
    def schemas(self) -> list[dict]:
        """OpenAI function-calling schemas for delegate / remember / recall."""
        return SCHEMAS

    async def execute(self, name: str, arguments: dict) -> str:
        """Run one tool. Returns a SHORT Ukrainian sentence for the voice
        model to speak. Never raises into the voice loop."""
        try:
            if name == "delegate":
                return self._delegate(arguments)
            if name == "remember":
                return await self._remember(arguments)
            if name == "recall":
                return await self._recall(arguments)
            return "Такої дії я не знаю."
        except Exception:
            logger.exception("[SPINE TOOLS] %s failed: %.200s", name, arguments)
            return "Не вийшло. Спробуй ще раз."

    def _delegate(self, arguments: dict) -> str:
        task = str(arguments.get("task", "")).strip()
        if not task:
            return "Скажи, що саме зробити."
        # Never awaited: start() returns immediately, the result re-enters
        # the conversation later through the wired ``deliver`` callback.
        self._hands.start(task)
        return "Прийнято, роблю."

    async def _remember(self, arguments: dict) -> str:
        content = str(arguments.get("content", "")).strip()
        if not content:
            return "Що саме запам'ятати?"
        type_str = str(arguments.get("type", "")).strip() or "fact"
        try:
            memory_type = MemoryType(type_str)
        except ValueError:
            memory_type = MemoryType.FACT
        entry = MemoryEntry(id="", type=memory_type, content=content)
        await self._memory.store(entry)
        return "Запам'ятав."

    async def _recall(self, arguments: dict) -> str:
        query = str(arguments.get("query", "")).strip()
        if not query:
            return "Що пошукати?"
        results = await self._memory.search(query, limit=3)
        if not results:
            return "Нічого не знайшов."
        facts = [str(getattr(r, "content", r)).strip() for r in results[:3]]
        facts = [f for f in facts if f]
        if not facts:
            return "Нічого не знайшов."
        return " ".join(facts)

    def cancel_all(self) -> int:
        """Stop everything Hands has in flight."""
        return self._hands.cancel_all()
