"""Shared mutable bot-speech state for open-mic barge-in.

The TranscriptionGateProcessor (pre-LLM) drops speech heard while the
bot is talking, because over loudspeakers the bot's own audio bleeds
back into the mic and STT transcribes it. To allow real barge-in
without headphones we must tell *the bot's own echo* apart from a
genuine human interruption. The cheapest reliable discriminator is
the text itself: if what was just transcribed closely matches what
the bot is currently saying, it's echo — otherwise it's a real
interruption.

The gate sits *upstream* of the LLM, so the bot's outbound text
(``LLMTextFrame`` chunks) never flows back through it. This module is
the single object the assistant-response logger (downstream, sees the
text stream) and the gate (upstream, makes the drop/interrupt call)
share — same pattern as ``LanguageState``.

Design notes mirror ``language_state``: plain-Python, non-async,
no Pipecat imports, single asyncio loop serialises producer/consumer.
"""

from __future__ import annotations

import logging


logger = logging.getLogger("heare.bot_speech_state")


class BotSpeechState:
    """Single source of truth for the bot's current spoken text.

    ``set_text`` is called incrementally as the LLM streams a response
    (and on standalone TTS speak). ``clear`` resets it at the start of
    a fresh response. Between the LLM finishing generation and TTS
    finishing playback the text is intentionally retained — the bot is
    still audibly speaking it, so echo comparison must still work.
    """

    def __init__(self) -> None:
        self._text: str = ""

    @property
    def text(self) -> str:
        """The bot's current/most-recent spoken text (may be empty)."""
        return self._text

    def set_text(self, text: str) -> None:
        self._text = text or ""

    def clear(self) -> None:
        self._text = ""


__all__ = ["BotSpeechState"]
