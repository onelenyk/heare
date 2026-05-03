"""Pre-TTS scrub processor — strips tool-name narration before TTS speaks it.

Sits BETWEEN ``llm_service`` (or ``assistant_response_logger``) and
``tts``. Mutates ``LLMTextFrame.text`` and ``TextFrame.text`` in-place
using :func:`text_scrub.scrub_tts_text`. Buffers per LLM response so
"the entire message is just a tool name" rules can fire on the
aggregated text — individual streamed chunks may each look harmless.

Why this exists: in production logs the LLM emitted ``list_tools`` and
``list_capabilities`` as plain assistant text instead of invoking the
functions. Without this processor those raw tool names are read aloud
by TTS, which is both confusing and a privacy leak (it reveals the
internal tool catalog to anyone within earshot).

Pipecat imports are deferred so admin CLI paths import this module
without portaudio.
"""
from __future__ import annotations

import logging
from typing import Any

from .text_scrub import scrub_tts_text


logger = logging.getLogger("heare.tts_scrub")


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
        """Strip tool-name narration from LLM-bound TTS text.

        Within an LLM response (between ``LLMFullResponseStartFrame``
        and ``LLMFullResponseEndFrame``) we BUFFER every ``LLMTextFrame``
        and ``TextFrame`` instead of forwarding them immediately. At
        the end-of-response boundary we run :func:`scrub_tts_text` on
        the joined text:

        * If the joined text scrubs to empty — i.e. the whole response
          was a tool-name-only narration like ``"list_tools"`` or even
          a chunked ``["list", "_tools"]`` — every buffered frame is
          flushed with ``frame.text = ""``. TTS sees the
          start/end boundary frames but produces silence.
        * Otherwise frames are forwarded with their per-frame scrubbed
          text.

        Buffering costs a small slice of TTS-latency (≈ the LLM stream
        duration) but is the only way to guarantee a chunked tool-name
        emission never reaches TTS. Voice-companion replies are short,
        so the cost is acceptable; correctness wins.

        Standalone ``TTSSpeakFrame`` payloads (startup greeting, etc.)
        bypass the LLM response cycle so we scrub them per-frame and
        forward immediately.
        """

        def __init__(self) -> None:
            super().__init__()
            self._buffered: list[Any] = []
            self._collecting = False

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)

            if isinstance(frame, LLMFullResponseStartFrame):
                # Forward the start frame so TTS knows a response is
                # opening — but begin buffering text.
                self._buffered = []
                self._collecting = True
                await self.push_frame(frame, direction)
                return

            if isinstance(frame, LLMFullResponseEndFrame):
                # End of response. Decide on the buffered text.
                self._collecting = False
                buffered = self._buffered
                self._buffered = []

                joined = "".join(getattr(f, "text", "") or "" for f in buffered)
                drop_whole = bool(joined.strip()) and scrub_tts_text(joined) == ""
                if drop_whole:
                    logger.info(
                        "[TTS SCRUB] dropping response — joined text is "
                        "tool-name only: %r",
                        joined[:80],
                    )

                for f in buffered:
                    if drop_whole:
                        f.text = ""
                    else:
                        raw = getattr(f, "text", "") or ""
                        cleaned = scrub_tts_text(raw)
                        if cleaned != raw:
                            logger.debug(
                                "scrubbed LLM text: %r -> %r",
                                raw[:60], cleaned[:60],
                            )
                        f.text = cleaned
                    await self.push_frame(f, direction)

                await self.push_frame(frame, direction)
                return

            if self._collecting and isinstance(frame, (LLMTextFrame, TextFrame)):
                # Hold the frame until end-of-response so the
                # whole-message check can see all chunks together.
                self._buffered.append(frame)
                return

            if isinstance(frame, TTSSpeakFrame):
                raw = getattr(frame, "text", "") or ""
                cleaned = scrub_tts_text(raw)
                if cleaned != raw:
                    logger.debug(
                        "scrubbed TTSSpeak: %r -> %r", raw[:60], cleaned[:60]
                    )
                frame.text = cleaned

            await self.push_frame(frame, direction)

    _processor_cls = TTSScrubProcessor
    return _processor_cls


def create_tts_scrub_processor():
    """Factory returning a TTSScrubProcessor instance."""
    cls = _build_processor_class()
    return cls()


__all__ = ["create_tts_scrub_processor"]
