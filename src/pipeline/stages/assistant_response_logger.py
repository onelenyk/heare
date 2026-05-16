"""AssistantResponseProcessor — log spoken bot responses to transcripts.

Sits BETWEEN the LLM service and the TTS service so it sees the LLM's text
output stream before TTS consumes it. We accumulate ``LLMTextFrame``/
``TextFrame`` chunks between ``LLMFullResponseStartFrame`` and
``LLMFullResponseEndFrame`` and log a single ``speaker_id='bot'`` row per
response.

We also log standalone ``TTSSpeakFrame`` payloads (e.g. the startup
greeting or other explicit one-shot speech that bypasses the LLM).

Pipecat imports are deferred so the module can be imported in tests
without pulling the full stack.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.config import Settings
    from src.store.storage import TranscriptStore


logger = logging.getLogger("heare.assistant_response")


def _load_pipecat_base():
    from pipecat.frames.frames import (
        Frame,
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
        TextFrame,
        TTSSpeakFrame,
    )
    from pipecat.processors.frame_processor import FrameProcessor

    return (
        FrameProcessor,
        Frame,
        LLMFullResponseStartFrame,
        LLMFullResponseEndFrame,
        LLMTextFrame,
        TextFrame,
        TTSSpeakFrame,
    )


_logger_cls: type | None = None


def _build_logger_class():
    global _logger_cls
    if _logger_cls is not None:
        return _logger_cls

    (
        FrameProcessor,
        Frame,
        LLMFullResponseStartFrame,
        LLMFullResponseEndFrame,
        LLMTextFrame,
        TextFrame,
        TTSSpeakFrame,
    ) = _load_pipecat_base()

    class AssistantResponseProcessor(FrameProcessor):  # type: ignore[misc,valid-type]
        """Accumulate LLM text per response, log on response-end."""

        def __init__(
            self,
            *,
            store: "TranscriptStore | None" = None,
            settings: "Settings | None" = None,
            session_state: Any = None,
        ) -> None:
            super().__init__()
            self.store = store
            self.settings = settings
            # The text-response channel: every LLM answer is persisted
            # here tagged with the active mode and whether TTS spoke it,
            # so consumers (dashboard, future Telegram/web) read the
            # agent's words without coupling to the speech path.
            self.session_state = session_state
            self._buffer: list[str] = []
            self._collecting = False

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)

            if isinstance(frame, LLMFullResponseStartFrame):
                self._buffer = []
                self._collecting = True

            elif isinstance(frame, LLMFullResponseEndFrame):
                if self._collecting:
                    self._collecting = False
                    spoken = "".join(self._buffer).strip()
                    self._buffer = []
                    await self._log(spoken)

            elif self._collecting and isinstance(
                frame, (LLMTextFrame, TextFrame)
            ):
                # LLMTextFrame chunks include their own inter-frame spaces
                # (LLMTextFrame.__post_init__ sets includes_inter_frame_spaces=True),
                # so a plain join preserves the original phrasing.
                text = getattr(frame, "text", "") or ""
                if text:
                    self._buffer.append(text)

            elif isinstance(frame, TTSSpeakFrame):
                # Standalone speak (e.g. startup greeting). Log it directly —
                # it's not part of an LLM response cycle.
                text = getattr(frame, "text", "") or ""
                await self._log(text.strip())

            await self.push_frame(frame, direction)

        async def _log(self, text: str) -> None:
            if not text or self.store is None:
                return
            agent_mode: str | None = None
            agent_spoken: bool | None = None
            if self.session_state is not None:
                try:
                    profile = self.session_state.profile
                    agent_mode = profile.name
                    agent_spoken = not profile.mute_output
                except Exception:
                    agent_mode = None
                    agent_spoken = None
            try:
                await self.store.log_transcript(
                    text=text,
                    mode="assistant",
                    speaker_id="bot",
                    agent_mode=agent_mode,
                    agent_spoken=agent_spoken,
                )
                logger.debug("logged bot response: %s", text[:60])
            except Exception:
                logger.exception("failed to log bot response")

    _logger_cls = AssistantResponseProcessor
    return _logger_cls


def create_assistant_response_logger(
    *,
    store: "TranscriptStore | None" = None,
    settings: "Settings | None" = None,
    session_state: Any = None,
) -> Any:
    """Factory returning an AssistantResponseProcessor instance.

    Insert BETWEEN the LLM service and the TTS service so the processor
    sees the LLM's text stream (LLMFullResponseStartFrame, LLMTextFrame /
    TextFrame, LLMFullResponseEndFrame) before TTS consumes it.
    """
    cls = _build_logger_class()
    return cls(store=store, settings=settings, session_state=session_state)


__all__ = ["create_assistant_response_logger"]
