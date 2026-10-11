"""A model in the form the core asks for.

The provider, the model and the key are looked up on every reply, so a
change in settings is heard on the next turn without a restart. A chosen
provider with no key falls back to one that has, loudly (``resolve_llm``).
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from heare.model.llm import resolve_llm, stream_chat_events


class Chat:
    def __init__(self, settings: Any, *, provider: str | None = None) -> None:
        """provider: pin this place to one provider; None follows settings."""
        self._settings = settings
        self._provider = provider

    def reply(self, messages: list[dict], verbs: list[dict]) -> AsyncIterator[dict]:
        cfg = resolve_llm(self._settings, self._provider)
        return stream_chat_events(messages, cfg, tools=verbs or None)
