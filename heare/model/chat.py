"""A model in the form the core asks for.

The provider, the model and the key are looked up on every reply, so a
change in settings is heard on the next turn without a restart. A chosen
provider with no key falls back to one that has, loudly (``resolve_llm``).
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

import httpx

from heare.model.llm import resolve_llm, stream_chat_events

logger = logging.getLogger("heare.model")


class Chat:
    def __init__(self, settings: Any, *, provider: str | None = None) -> None:
        """provider: pin this place to one provider; None follows settings."""
        self._settings = settings
        self._provider = provider

    async def reply(self, messages: list[dict], verbs: list[dict]) -> AsyncIterator[dict]:
        """One more try when the connection drops before a single word:
        a dropped socket is not a reason to say «щось пішло не так». Once
        something has been said, a break is a break."""
        for attempt in (1, 2):
            cfg = resolve_llm(self._settings, self._provider)
            started = False
            try:
                async for event in stream_chat_events(messages, cfg, tools=verbs or None):
                    started = True
                    yield event
                return
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if started or attempt == 2:
                    raise
                logger.warning("model connection failed (%s) — trying once more", exc)
