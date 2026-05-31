"""VoiceOutputProcessor — converts VoiceContentFrame into TTS speech.

Consumes ``VoiceContentFrame`` emitted by the ``OutputRouter`` when the
LLM's response includes a ``[voice]`` tag.  The processor checks voice
availability (mode profile + mute flag), then emits a ``TTSSpeakFrame``
that flows downstream through the existing TTS chain (tts_scrub →
EdgeTTSService → tts_fade → mute_gate).

When voice is unavailable (silent/meeting mode, or mute_bot active)
the frame is dropped with a debug log.

All other frames pass through unchanged — this processor only intercepts
``VoiceContentFrame``.

Pipecat imports are deferred so the module can be imported in tests
without pulling the full stack.
"""
from __future__ import annotations

import logging
from typing import Any

from src.pipeline.stages.output_router import VoiceContentFrame

logger = logging.getLogger("heare.voice_output")


# ---------------------------------------------------------------------------
# Processor — deferred import for portaudio-free imports.
# ---------------------------------------------------------------------------


_processor_cls: type | None = None


def _build_processor_class():
    global _processor_cls
    if _processor_cls is not None:
        return _processor_cls

    from pipecat.frames.frames import TTSSpeakFrame
    from pipecat.processors.frame_processor import FrameProcessor

    class VoiceOutputProcessor(FrameProcessor):  # type: ignore[misc,valid-type]
        """Convert ``VoiceContentFrame`` into ``TTSSpeakFrame`` for TTS.

        Parameters
        ----------
        tts
            The active TTS service (EdgeTTSService).  Stored for
            future introspection; the actual audio generation happens
            in downstream pipeline stages.
        scrub
            Optional ``TTSScrubProcessor`` instance.  Passed for
            the factory contract; scrubbing is handled downstream.
        fade
            Optional ``_TtsFadeOnInterruption`` observer.  Passed for
            the factory contract; fade is handled downstream.
        mute
            Optional ``MuteGateProcessor``.  Passed for the factory
            contract; mute is handled downstream.
        session_state
            Optional ``SessionState`` for checking voice availability
            per the active mode profile (``voice_muted``,
            ``outputs``).
        """

        def __init__(
            self,
            *,
            tts: Any = None,
            scrub: Any = None,
            fade: Any = None,
            mute: Any = None,
            session_state: Any = None,
        ) -> None:
            super().__init__()
            self._tts = tts
            self._scrub = scrub
            self._fade = fade
            self._mute = mute
            self._session_state = session_state

        # ------------------------------------------------------------------
        # Voice availability gate
        # ------------------------------------------------------------------

        def _voice_available(self) -> bool:
            """Return ``True`` if the active mode profile allows voice output.

            Voice is blocked when:
            * ``voice_muted`` is ``True`` on the mode profile (silent,
              meeting — a hard mechanical gate, not a prompt hint).
            * ``"voice"`` is not in ``profile.outputs``.

            Returns ``True`` when session_state is ``None`` (allows
            voice by default — graceful for tests).
            """
            if self._session_state is None:
                return True
            try:
                profile = self._session_state.profile
                if profile.voice_muted:
                    logger.debug("Voice blocked: voice_muted=True")
                    return False
                if "voice" not in profile.outputs:
                    logger.debug(
                        "Voice blocked: mode=%s outputs=%s",
                        profile.name,
                        set(profile.outputs),
                    )
                    return False
            except Exception:
                logger.exception(
                    "voice_available check failed (non-fatal); allowing voice"
                )
                return True
            return True

        # ------------------------------------------------------------------
        # Frame processing
        # ------------------------------------------------------------------

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)

            if not isinstance(frame, VoiceContentFrame):
                # Passthrough: InterruptionFrame, StartFrame, EndFrame,
                # TextContentFrame, CanvasContentFrame, etc.
                await self.push_frame(frame, direction)
                return

            text = getattr(frame, "text", "") or ""
            if not text:
                # Empty voice tag — nothing to speak.
                logger.debug("VoiceContentFrame with empty text, skipping")
                return

            if not self._voice_available():
                logger.info(
                    "Voice output dropped (voice unavailable): %s",
                    text[:80],
                )
                return

            logger.debug("Voice output: %s", text[:80])
            speak = TTSSpeakFrame(text=text)
            await self.push_frame(speak, direction)

    _processor_cls = VoiceOutputProcessor
    return _processor_cls


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_voice_output(
    tts_service: Any,
    tts_scrub: Any,
    tts_fade: Any,
    mute_gate: Any,
    *,
    session_state: Any = None,
) -> Any:
    """Factory returning a ``VoiceOutputProcessor`` instance.

    Insert this processor after the ``OutputRouter`` so it receives
    typed ``VoiceContentFrame`` and converts it into the
    ``TTSSpeakFrame`` that the downstream TTS stages consume.

    Parameters
    ----------
    tts_service
        The active TTS service instance (e.g. ``EdgeTTSService``).
    tts_scrub
        The ``TTSScrubProcessor`` instance.  Scrub logic is applied
        downstream; the reference is stored for the factory contract.
    tts_fade
        The ``_TtsFadeOnInterruption`` observer.  Fade logic is
        applied downstream; the reference is stored for the factory
        contract.
    mute_gate
        The ``MuteGateProcessor`` instance.  Mute logic is applied
        downstream; the reference is stored for the factory contract.
    session_state
        Optional ``SessionState`` for voice availability gating.
        When ``None``, voice is always allowed (graceful for tests).
    """
    cls = _build_processor_class()
    return cls(
        tts=tts_service,
        scrub=tts_scrub,
        fade=tts_fade,
        mute=mute_gate,
        session_state=session_state,
    )


__all__ = ["create_voice_output"]
