"""TranscriptionGateProcessor — pre-LLM orchestration.

The gate sits between STT and the LLM context aggregator and owns
every non-LLM decision that has to fire before the LLM turn runs:

* feedback-loop guard — drop transcripts while the bot is speaking,
  while a non-speech indication cue is playing, while voice enrollment
  is active, or during the post-bot cooldown window.
* STT debounce — coalesce ``TranscriptionFrame`` events arriving
  inside ``settings.transcript_debounce_seconds``.
* language hysteresis — 2-turn confirmation before swapping the
  active language; writes the result into the shared
  ``LanguageState`` so the LLM service / system-prompt injector can
  read the active language without coupling back to this processor.
* TTS voice swap — call ``tts_service.set_voice(...)`` whenever the
  active language changes.
* transcript logging — persist to ``TranscriptStore`` so the watch
  dashboard sees user activity.
* cancel keyword fast-path — when the smart standalone-imperative
  detector fires, push an ``InterruptionFrame`` upstream so Pipecat
  cancels any in-flight ``register_function`` call and the TTS fade
  observer mutes the speaker.

When all guards pass, the gate pushes the ``TranscriptionFrame``
downstream — letting the LLM context aggregator drive the LLM turn.
The gate itself never speaks to the LLM.

Pipecat imports are deferred so the admin CLI paths (which import
this module transitively) work on machines without portaudio.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from .language import (
    detect_language_from_frame,
    is_standalone_cancel_imperative,
    voice_for_language,
)
from .language_state import LanguageState

if TYPE_CHECKING:
    from .config import Settings
    from .storage import TranscriptStore


logger = logging.getLogger("heare.transcription_gate")


# Default stop-word list used when the gate is constructed without a
# Settings object (test paths). Mirrors src/config.py:cancel_stop_words.
_DEFAULT_STOP_WORDS: tuple[str, ...] = (
    "stop",
    "cancel",
    "halt",
    "відміни",
    "отмени",
    "стоп",
)


def _load_pipecat_base():
    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        Frame,
        InterruptionFrame,
        TranscriptionFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    from .indication import IndicationCueFrame

    return (
        FrameProcessor,
        FrameDirection,
        Frame,
        TranscriptionFrame,
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        IndicationCueFrame,
        InterruptionFrame,
    )


_gate_cls: type | None = None


def _build_transcription_gate_class():
    global _gate_cls
    if _gate_cls is not None:
        return _gate_cls
    (
        FrameProcessor,
        FrameDirection,
        Frame,
        TranscriptionFrame,
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        IndicationCueFrame,
        InterruptionFrame,
    ) = _load_pipecat_base()

    class TranscriptionGateProcessor(FrameProcessor):  # type: ignore[misc,valid-type]
        def __init__(
            self,
            *,
            store: "TranscriptStore | None" = None,
            settings: "Settings | None" = None,
            tts_service: Any = None,
            language_state: LanguageState | None = None,
        ) -> None:
            super().__init__()
            self.store = store
            self.settings = settings
            self._tts_service = tts_service

            self._current_voice: str = (
                settings.tts_voice if settings is not None else "en-US-AriaNeural"
            )
            _default_lang = (
                settings.groq_language
                if settings is not None
                and settings.groq_language not in ("auto", "")
                else "en"
            )
            self._active_lang: str = _default_lang
            self._pending_lang: str | None = None
            self._pending_lang_count: int = 0
            self._language_state: LanguageState | None = language_state
            if self._language_state is not None:
                # Seed the shared state so consumers see the gate's
                # default before any utterance arrives.
                self._language_state.set_language(self._active_lang)

            self._bot_speaking = False
            self._bot_cooldown_until = 0.0
            self._indication_speaking = False
            self._bot_cooldown_seconds = (
                settings.bot_speaking_cooldown_seconds
                if settings is not None
                else 2.0
            )

            self._debounce_seconds: float = (
                settings.transcript_debounce_seconds
                if settings is not None
                else 0.0
            )
            self._debounce_buffer: list[str] = []
            self._debounce_frame: Any | None = None
            self._debounce_direction: Any | None = None
            import asyncio as _asyncio
            self._debounce_task: _asyncio.Task | None = None

        @property
        def active_language(self) -> str:
            """Active language tag (e.g. 'en', 'uk', 'ru'). PH2-04 will
            move this onto a shared LanguageState; the property gives
            consumers a stable read API today.
            """
            return self._active_lang

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)

            if isinstance(frame, BotStartedSpeakingFrame):
                self._bot_speaking = True
                await self.push_frame(frame, direction)
                return
            if isinstance(frame, BotStoppedSpeakingFrame):
                self._bot_speaking = False
                self._bot_cooldown_until = (
                    time.monotonic() + self._bot_cooldown_seconds
                )
                await self.push_frame(frame, direction)
                return
            if isinstance(frame, IndicationCueFrame):
                self._indication_speaking = bool(frame.start)
                await self.push_frame(frame, direction)
                return

            if isinstance(frame, TranscriptionFrame):
                if self._debounce_seconds > 0:
                    await self._schedule_transcription(frame, direction)
                else:
                    await self._handle_transcription(frame, direction)
                return

            await self.push_frame(frame, direction)

        async def _schedule_transcription(
            self, frame: Any, direction: Any
        ) -> None:
            """Buffer a TranscriptionFrame; debounce-fire after silence window."""
            import asyncio as _asyncio

            text = (frame.text or "").strip()
            if text:
                self._debounce_buffer.append(text)
            self._debounce_frame = frame
            self._debounce_direction = direction
            if (
                self._debounce_task is not None
                and not self._debounce_task.done()
            ):
                self._debounce_task.cancel()
            self._debounce_task = _asyncio.create_task(self._flush_debounced())

        async def _flush_debounced(self) -> None:
            """Wait debounce window, then handle the combined transcript."""
            import asyncio as _asyncio

            try:
                await _asyncio.sleep(self._debounce_seconds)
            except _asyncio.CancelledError:
                return
            combined = " ".join(self._debounce_buffer).strip()
            frame = self._debounce_frame
            direction = self._debounce_direction
            self._debounce_buffer = []
            self._debounce_frame = None
            self._debounce_direction = None
            if not combined or frame is None:
                return
            await self._handle_transcription(
                frame, direction, override_text=combined
            )

        async def _handle_transcription(
            self,
            frame: Any,
            direction: Any,
            override_text: str | None = None,
        ) -> None:
            source_text = (
                override_text if override_text is not None else (frame.text or "")
            )
            transcript = source_text.strip()
            if not transcript:
                return

            from .indication import is_enrollment_active
            enrollment_active = is_enrollment_active()
            if (
                self._bot_speaking
                or self._indication_speaking
                or enrollment_active
                or time.monotonic() < self._bot_cooldown_until
            ):
                logger.debug(
                    "transcription_gate: dropping transcript (bot=%s indication=%s enrollment=%s): %r",
                    self._bot_speaking,
                    self._indication_speaking,
                    enrollment_active,
                    transcript[:60],
                )
                return

            # Language detection + 2-turn hysteresis (US-I18N-03/05)
            raw_lang = detect_language_from_frame(
                frame, fallback=self._active_lang
            )
            if raw_lang == self._active_lang:
                self._pending_lang = None
                self._pending_lang_count = 0
            elif raw_lang == self._pending_lang:
                self._pending_lang_count += 1
                if self._pending_lang_count >= 2:
                    self._active_lang = raw_lang
                    self._pending_lang = None
                    self._pending_lang_count = 0
                    if self._language_state is not None:
                        self._language_state.set_language(raw_lang)
            else:
                self._pending_lang = raw_lang
                self._pending_lang_count = 1

            # Voice swap follows the active (post-hysteresis) language.
            self._set_tts_voice(self._active_lang)

            # Persist transcript so the watch dashboard sees user activity.
            if self.store is not None and self.settings is not None:
                try:
                    await self.store.log_transcript(
                        transcript,
                        self.settings.mode.value,
                        speaker_id=getattr(frame, "speaker_id", None),
                        speaker_confidence=getattr(
                            frame, "speaker_confidence", None
                        ),
                    )
                except Exception:
                    logger.exception(
                        "transcription_gate: failed to log transcript "
                        "(non-fatal)"
                    )

            # Cancel keyword fast-path (PH2-05): the smart detector
            # (US-WU-02) is shared by both the legacy and Pipecat-native
            # paths. Native path: push InterruptionFrame upstream so
            # Pipecat cancels in-flight LLM/TTS plus any in-flight
            # register_function call (e.g. bash → CancelledError →
            # os.killpg in execute_direct). Legacy path: also call
            # IntentQueue.cancel_active() when an intent_queue is
            # plumbed in for backward compatibility — main.py's
            # post-PH2-06 rewrite drops the queue entirely.
            stop_words = (
                tuple(self.settings.cancel_stop_words)
                if self.settings is not None
                else _DEFAULT_STOP_WORDS
            )
            cancel_detected = is_standalone_cancel_imperative(
                transcript, stop_words
            )
            if cancel_detected:
                logger.info(
                    "[CANCEL FAST-PATH transcript=%r]", transcript[:80]
                )
                # Native interruption: push the frame upstream so the
                # transport sees it before downstream TTS/LLM frames.
                # Pipecat's pipeline routes SystemFrames immediately,
                # bypassing per-stage queues.
                try:
                    await self.push_frame(
                        InterruptionFrame(), FrameDirection.UPSTREAM
                    )
                except Exception:
                    logger.exception(
                        "transcription_gate: InterruptionFrame push "
                        "failed (non-fatal)"
                    )
                # On cancel we do NOT push the transcript downstream —
                # there is no LLM turn to drive; the user said "stop",
                # not a new request.
                return

            # Push the (possibly coalesced) transcript downstream so the
            # LLM context aggregator can drive the turn. When override_text
            # was set by the debounce flush, mint a fresh frame carrying
            # the combined text — never mutate the inbound frame.
            if override_text is not None and transcript != (frame.text or ""):
                outbound = self._clone_with_text(frame, transcript)
            else:
                outbound = frame
            await self.push_frame(outbound, direction)

        @staticmethod
        def _clone_with_text(frame: Any, text: str) -> Any:
            """Return a TranscriptionFrame copy with overridden text.

            Pipecat dataclasses don't expose ``replace`` reliably across
            versions, so use ``dataclasses.replace`` when available and
            fall back to attribute mutation on a shallow copy.
            """
            try:
                from dataclasses import is_dataclass, replace as dc_replace

                if is_dataclass(frame):
                    return dc_replace(frame, text=text)
            except Exception:
                logger.debug(
                    "transcription_gate: dataclass.replace failed, "
                    "falling back to copy"
                )
            import copy

            clone = copy.copy(frame)
            try:
                clone.text = text
            except Exception:
                logger.exception(
                    "transcription_gate: cannot override frame text; "
                    "passing through original"
                )
                return frame
            return clone

        def _set_tts_voice(self, lang: str) -> None:
            if self._tts_service is None:
                return
            new_voice = voice_for_language(lang)
            if new_voice == self._current_voice:
                return
            old_voice = self._current_voice
            self._tts_service.set_voice(new_voice)
            self._current_voice = new_voice
            logger.info(
                "[TTS VOICE SWAP] from=%s to=%s lang=%s",
                old_voice,
                new_voice,
                lang,
            )

    _gate_cls = TranscriptionGateProcessor
    return _gate_cls


def create_transcription_gate(
    *,
    store: "TranscriptStore | None" = None,
    settings: "Settings | None" = None,
    tts_service: Any = None,
    language_state: LanguageState | None = None,
):
    """Factory returning a TranscriptionGateProcessor instance."""
    cls = _build_transcription_gate_class()
    return cls(
        store=store,
        settings=settings,
        tts_service=tts_service,
        language_state=language_state,
    )


__all__ = [
    "create_transcription_gate",
]
