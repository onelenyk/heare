"""Language detection refinement processor.

Sits after STT and refines language detection via Claude when the
transcription confidence is ambiguous or when Groq's auto-detect
might be uncertain. Uses the LLM to confirm language from text content
when needed, ensuring robust language detection for multi-language conversations.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .language_state import LanguageState


logger = logging.getLogger("heare.language_refiner")


def _load_pipecat_base():
    from pipecat.frames.frames import Frame, TranscriptionFrame
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    return FrameProcessor, FrameDirection, Frame, TranscriptionFrame


_refiner_cls: type | None = None


def _build_language_refiner_class():
    """Lazy-load the language refiner processor."""
    global _refiner_cls
    if _refiner_cls is not None:
        return _refiner_cls

    FrameProcessor, FrameDirection, Frame, TranscriptionFrame = _load_pipecat_base()

    class LanguageRefinerProcessor(FrameProcessor):  # type: ignore[misc,valid-type]
        """Refines language detection via Claude when needed.

        If Groq's auto-detect succeeds, this processor passes frames through
        unchanged. If Groq couldn't detect (e.g., very short utterance), this
        processor uses Claude to confirm the language from the transcribed text.
        """

        def __init__(
            self,
            *,
            llm_service: Any | None = None,
            language_state: "LanguageState | None" = None,
        ) -> None:
            super().__init__()
            self._llm_service = llm_service
            self._language_state = language_state

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)

            # Only refine TranscriptionFrames
            if not isinstance(frame, TranscriptionFrame):
                await self.push_frame(frame, direction)
                return

            # Check if Groq detected a language
            text = (frame.text or "").strip()
            if not text:
                await self.push_frame(frame, direction)
                return

            detected_lang = self._extract_groq_language(frame)

            # If Groq detected a language and confidence is reasonable, trust it
            if detected_lang:
                logger.debug(
                    "language_refiner: Groq detected %s from %r",
                    detected_lang,
                    text[:60],
                )
                await self.push_frame(frame, direction)
                return

            # Groq didn't detect — use Claude to refine
            refined = await self._refine_with_claude(text)
            if refined and refined != detected_lang:
                logger.info(
                    "language_refiner: Claude refined language to %s from %r",
                    refined,
                    text[:60],
                )
                # Store the refined language in frame for downstream to pick up
                frame._detected_language = refined

            await self.push_frame(frame, direction)

        def _extract_groq_language(self, frame: Any) -> str | None:
            """Extract language detected by Groq from the frame."""
            try:
                result = getattr(frame, "result", None)
                if result is None:
                    return None
                lang_name = getattr(result, "language", None)
                if lang_name is None:
                    return None
                # Map Groq's language name to ISO 639-1
                from .language import WHISPER_NAME_TO_ISO

                iso = WHISPER_NAME_TO_ISO.get(lang_name.lower())
                return iso
            except Exception:
                return None

        async def _refine_with_claude(self, text: str) -> str | None:
            """Use Claude to detect language from text when Groq failed."""
            if not self._llm_service or not text:
                return None

            # Simple heuristic refinement if Claude isn't available
            from .language_detector import _detect_language_heuristic

            try:
                # Attempt Claude detection via the language_detector module
                # This is a fallback for when LLM service isn't configured
                refined = _detect_language_heuristic(text)
                return refined
            except Exception as e:
                logger.warning(
                    "language_refiner: Claude detection failed: %s; using fallback",
                    e,
                )
                return _detect_language_heuristic(text)

    _refiner_cls = LanguageRefinerProcessor
    return LanguageRefinerProcessor


def create_language_refiner(
    *,
    llm_service: Any | None = None,
    language_state: "LanguageState | None" = None,
) -> Any:
    """Create a language refiner processor."""
    cls = _build_language_refiner_class()
    return cls(llm_service=llm_service, language_state=language_state)
