"""Pre-TTS scrub processor — strips tool-name narration before TTS speaks it.

Sits BETWEEN ``llm_service`` (or ``assistant_response_logger``) and
``tts``. Mutates ``LLMTextFrame.text`` and ``TextFrame.text`` in-place
using :func:`text_scrub.scrub_tts_text`. Buffers per LLM response so
"the entire message is just a tool name" rules can fire on the
aggregated text — individual streamed chunks may each look harmless.

Why this exists: in production logs the LLM emitted ``list_tools`` and
``list_capabilities`` as plain assistant text instead of invoking the
functions. Without this processor those raw tool names are read aloud
by TTS, which is both confusing and a privacy leak.

Pipecat imports are deferred so admin CLI paths import this module
without portaudio. The deferred-import scaffold is the small closure
at the bottom; all logic lives in module-level functions above it.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from src.pipeline.stages.text_scrub import scrub_tts_text
from src.voice.language.core import detect_language_from_text


logger = logging.getLogger("heare.tts_scrub")


def _select_voice_for_text(
    text: str,
    voice_setter: "Callable[[str], None] | None",
    fallback: "Callable[[], str] | None",
) -> None:
    """Set the TTS voice from the language of the text about to be spoken.

    The voice MUST follow the assistant's output, not the user's input.
    When the two differ — the user types English but the agent is
    constrained to answer in Ukrainian — a voice picked from the input is
    wrong, and wrong here is not an accent but silence: Edge TTS raises
    ``NoAudioReceived`` for Cyrillic on an English voice, so the reply is
    synthesised to 0 bytes and never heard. This is the last point before
    TTS where the real spoken text is known, so it is where the voice is
    decided.
    """
    if voice_setter is None:
        return
    text = (text or "").strip()
    if not text:
        return
    fb = "en"
    if fallback is not None:
        try:
            fb = fallback() or "en"
        except Exception:
            fb = "en"
    try:
        lang = detect_language_from_text(text, fallback=fb)
        voice_setter(lang)
    except Exception:
        # A voice we cannot set is not worth dropping the response over.
        logger.exception("tts_scrub: voice selection failed (non-fatal)")


# ---------------------------------------------------------------------------
# Pure logic — no pipecat dependency, easy to read and test in isolation.
# ---------------------------------------------------------------------------


def _scrub_buffered_response(buffered: list[Any]) -> None:
    """Mutate every buffered LLM-response frame's ``text`` in place.

    If the joined text scrubs to empty (the whole response was tool-name
    narration like ``list_tools`` or a chunked ``["list", "_tools"]``),
    every frame's text is set to ``""`` so TTS produces silence between
    the start/end boundary frames. Otherwise each frame's text is
    individually scrubbed.
    """
    joined = "".join(getattr(f, "text", "") or "" for f in buffered)
    drop_whole = bool(joined.strip()) and scrub_tts_text(joined) == ""
    if drop_whole:
        logger.info(
            "[TTS SCRUB] dropping response — joined text is tool-name only: %r",
            joined[:80],
        )

    for f in buffered:
        if drop_whole:
            f.text = ""
            continue
        raw = getattr(f, "text", "") or ""
        cleaned = scrub_tts_text(raw)
        if cleaned != raw:
            logger.debug("scrubbed LLM text: %r -> %r", raw[:60], cleaned[:60])
        f.text = cleaned


def _scrub_speak_frame(frame: Any) -> None:
    """Mutate a standalone ``TTSSpeakFrame`` (startup greeting, etc.)."""
    raw = getattr(frame, "text", "") or ""
    cleaned = scrub_tts_text(raw)
    if cleaned != raw:
        logger.debug("scrubbed TTSSpeak: %r -> %r", raw[:60], cleaned[:60])
    frame.text = cleaned


# ---------------------------------------------------------------------------
# Pipecat-bound class — lives in a closure so the import is deferred.
# ---------------------------------------------------------------------------


_processor_cls: type | None = None


def _build_processor_class():
    global _processor_cls
    if _processor_cls is not None:
        return _processor_cls

    from pipecat.frames.frames import (
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
        TextFrame,
        TTSSpeakFrame,
    )
    from pipecat.processors.frame_processor import FrameProcessor

    class TTSScrubProcessor(FrameProcessor):  # type: ignore[misc,valid-type]
        """Buffer LLM-response text and scrub tool-name narration before TTS.

        Also the single owner of TTS voice selection: the voice is set from
        the language of the fully assembled, scrubbed response text right
        before it is pushed to TTS — see :func:`_select_voice_for_text`.
        """

        def __init__(
            self,
            *,
            voice_setter: "Callable[[str], None] | None" = None,
            language_fallback: "Callable[[], str] | None" = None,
        ) -> None:
            super().__init__()
            self._buffered: list[Any] = []
            self._collecting = False
            self._voice_setter = voice_setter
            self._language_fallback = language_fallback

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)

            if isinstance(frame, LLMFullResponseStartFrame):
                self._buffered = []
                self._collecting = True
                await self.push_frame(frame, direction)
                return

            if isinstance(frame, LLMFullResponseEndFrame):
                self._collecting = False
                _scrub_buffered_response(self._buffered)
                # Voice follows the OUTPUT: pick it from the assembled reply
                # text before any frame reaches TTS. The frames are buffered
                # here until now, so TTS has seen none of them yet.
                joined = "".join(
                    getattr(f, "text", "") or "" for f in self._buffered
                )
                _select_voice_for_text(
                    joined, self._voice_setter, self._language_fallback
                )
                for f in self._buffered:
                    await self.push_frame(f, direction)
                self._buffered = []
                await self.push_frame(frame, direction)
                return

            if self._collecting and isinstance(frame, (LLMTextFrame, TextFrame)):
                self._buffered.append(frame)
                return

            if isinstance(frame, TTSSpeakFrame):
                _scrub_speak_frame(frame)
                # Standalone speak (startup greeting, cues) bypasses the LLM
                # response cycle, so give it the same output-driven voice.
                _select_voice_for_text(
                    getattr(frame, "text", "") or "",
                    self._voice_setter,
                    self._language_fallback,
                )

            await self.push_frame(frame, direction)

    _processor_cls = TTSScrubProcessor
    return _processor_cls


def create_tts_scrub_processor(
    *,
    voice_setter: "Callable[[str], None] | None" = None,
    language_fallback: "Callable[[], str] | None" = None,
):
    """Factory returning a TTSScrubProcessor instance.

    ``voice_setter`` is normally the transcription gate's ``_set_tts_voice``
    so the gate stays the one place that tracks the current voice; passing it
    here makes the *output* text drive the choice. ``language_fallback`` is
    consulted only when the response carries no script signal (digits,
    punctuation) — normally the conversation's current language.
    """
    cls = _build_processor_class()
    return cls(voice_setter=voice_setter, language_fallback=language_fallback)


__all__ = ["create_tts_scrub_processor"]
