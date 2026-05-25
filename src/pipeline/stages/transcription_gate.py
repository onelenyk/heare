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

from src.voice.language.core import (
    detect_language_from_frame,
    is_standalone_cancel_imperative,
    voice_for_language,
)
from src.pipeline.language_state import LanguageState

if TYPE_CHECKING:
    from src.config import Settings
    from src.store.storage import TranscriptStore


logger = logging.getLogger("heare.transcription_gate")


# Audio-event forwarding policy. The detection threshold
# (settings.audio_event_threshold, default 0.4) decides when YAMNet
# *confirms* an event; this higher floor decides when that event is
# confident enough to tag a user turn for the LLM. Stale events are
# dropped: an old "Music" tag must not bleed onto an utterance spoken
# minutes later in silence.
_AUDIO_EVENT_FORWARD_MIN_SCORE: float = 0.7
_AUDIO_EVENT_MAX_AGE_S: float = 12.0


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


def _normalize_words(text: str) -> list[str]:
    """Lowercase, strip non-alphanumerics, split to word tokens."""
    out: list[str] = []
    for raw in text.lower().split():
        token = "".join(ch for ch in raw if ch.isalnum())
        if token:
            out.append(token)
    return out


def _is_echo(transcript: str, bot_text: str, ratio: float) -> bool:
    """True if ``transcript`` is likely the bot's own speech bleeding
    back through the mic rather than a genuine human barge-in.

    Two-level heuristic:

    1. **Word overlap** — fraction of transcript words that also appear in
       the bot's current spoken text (fast, works when both sides use the
       same script and the STT output is clean).
    2. **Character bigram overlap** — fallback when word-level fails.
       Handles script mismatch (e.g. Latin bot-name "VEX" → STT transcribes
       as Cyrillic "ВЕКС") and STT errors (e.g. "зв'язку" → "звяіску").
       Character n-grams are resilient to both because the echoed audio
       overwhelmingly shares the same character sequence regardless of
       script encoding or minor transcription drift.

    When the bot has said nothing yet there is nothing to echo,
    so it cannot be echo.
    """
    words = _normalize_words(transcript)
    if not words:
        return True
    bot_words_set = set(_normalize_words(bot_text))
    if not bot_words_set:
        return False

    # Level 1: word overlap
    hits = sum(1 for w in words if w in bot_words_set)
    if (hits / len(words)) >= ratio:
        return True

    # Level 2: character bigram overlap — robust to script mismatch
    # and STT errors that preserve the character skeleton of the text.
    bot_raw = bot_text.lower()
    tx_raw = transcript.lower()

    def _bigrams(text: str) -> set[str]:
        clean = "".join(ch for ch in text if ch.isalnum())
        return {clean[i : i + 2] for i in range(len(clean) - 1)}

    tx_bigrams = _bigrams(tx_raw)
    bt_bigrams = _bigrams(bot_raw)
    if not bt_bigrams:
        return False
    hits_bigram = len(tx_bigrams & bt_bigrams)
    return (hits_bigram / len(bt_bigrams)) >= ratio


def _load_pipecat_base():
    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        Frame,
        InterruptionFrame,
        TranscriptionFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    from src.voice.indication.core import IndicationCueFrame

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
            bot_speech_state: Any = None,
        ) -> None:
            super().__init__()
            self.store = store
            self.settings = settings
            self._tts_service = tts_service
            self._bot_speech_state = bot_speech_state
            self._barge_in_enabled = (
                settings.barge_in_enabled if settings is not None else True
            )
            self._barge_in_min_chars = (
                settings.barge_in_min_chars if settings is not None else 4
            )
            self._barge_in_echo_ratio = (
                settings.barge_in_echo_ratio
                if settings is not None
                else 0.6
            )

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
            """Buffer a TranscriptionFrame; debounce-fire after silence window.

            Cancel words bypass the debounce entirely and are handled
            immediately so the bot stops without the debounce delay.
            """
            import asyncio as _asyncio

            text = (frame.text or "").strip()
            if text:
                self._debounce_buffer.append(text)
            self._debounce_frame = frame
            self._debounce_direction = direction

            # Fast-path: check for cancel words before debouncing so the
            # bot stops immediately instead of waiting for the timer.
            stop_words = (
                tuple(self.settings.cancel_stop_words)
                if self.settings is not None
                else _DEFAULT_STOP_WORDS
            )
            if is_standalone_cancel_imperative(text, stop_words):
                # Cancel any pending debounce so stale subsequent frames
                # don't spawn a new LLM turn after we interrupt.
                if (
                    self._debounce_task is not None
                    and not self._debounce_task.done()
                ):
                    self._debounce_task.cancel()
                    self._debounce_task = None
                self._debounce_buffer = []
                await self._handle_transcription(
                    frame, direction, override_text=text
                )
                return

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

            # Cancel keyword fast-path (PH2-05): check BEFORE the bot-active
            # guard so cancel words ("stop", "стоп", etc.) always interrupt
            # the bot regardless of barge-in mode or bot-speaking state.
            stop_words = (
                tuple(self.settings.cancel_stop_words)
                if self.settings is not None
                else _DEFAULT_STOP_WORDS
            )
            if is_standalone_cancel_imperative(transcript, stop_words):
                logger.info(
                    "[CANCEL FAST-PATH] transcript=%r", transcript[:80]
                )
                try:
                    await self.push_frame(
                        InterruptionFrame(), FrameDirection.UPSTREAM
                    )
                except Exception:
                    logger.exception(
                        "transcription_gate: cancel InterruptionFrame "
                        "push failed (non-fatal)"
                    )
                # Also clear speaking state so the post-cancel silence
                # does not re-trigger the cooldown guard.
                self._bot_speaking = False
                self._bot_cooldown_until = 0.0
                return

            from src.voice.indication.core import is_enrollment_active
            enrollment_active = is_enrollment_active()

            # Hard block: never interrupt a sound cue or an active
            # speaker-enrollment flow — those are not conversational
            # turns and barging in would corrupt them.
            if self._indication_speaking or enrollment_active:
                logger.debug(
                    "transcription_gate: dropping transcript "
                    "(indication=%s enrollment=%s): %r",
                    self._indication_speaking,
                    enrollment_active,
                    transcript[:60],
                )
                return

            bot_active = (
                self._bot_speaking
                or time.monotonic() < self._bot_cooldown_until
            )
            if bot_active:
                # Without barge-in, preserve the legacy behaviour:
                # everything heard while the bot speaks is dropped.
                if not self._barge_in_enabled:
                    logger.debug(
                        "transcription_gate: dropping transcript "
                        "(bot speaking, barge-in disabled): %r",
                        transcript[:60],
                    )
                    return
                # Too short to be a real interruption — almost always a
                # noise blip or a one-word echo fragment.
                if len(transcript) < self._barge_in_min_chars:
                    logger.debug(
                        "transcription_gate: dropping transcript "
                        "(bot speaking, too short for barge-in): %r",
                        transcript,
                    )
                    return
                bot_text = (
                    self._bot_speech_state.text
                    if self._bot_speech_state is not None
                    else ""
                )
                if _is_echo(
                    transcript, bot_text, self._barge_in_echo_ratio
                ):
                    logger.debug(
                        "transcription_gate: dropping transcript "
                        "(echo of bot speech): %r",
                        transcript[:60],
                    )
                    return
                # Genuine barge-in: the human said something the bot is
                # not currently saying. Stop the bot (Pipecat routes the
                # SystemFrame immediately, cascading to the TTS fade /
                # ffmpeg kill) and fall through so this transcript drives
                # a fresh user turn.
                logger.info(
                    "[BARGE-IN] interrupting bot speech: %r",
                    transcript[:80],
                )
                try:
                    await self.push_frame(
                        InterruptionFrame(), FrameDirection.UPSTREAM
                    )
                except Exception:
                    logger.exception(
                        "transcription_gate: barge-in InterruptionFrame "
                        "push failed (non-fatal)"
                    )
                # Clear the speaking/cooldown state so the just-spoken
                # text does not re-block the turn we are about to drive.
                self._bot_speaking = False
                self._bot_cooldown_until = 0.0

            # Language detection + 2-turn hysteresis (US-I18N-03/05)
            raw_lang = detect_language_from_frame(
                frame, fallback=self._active_lang
            )
            logger.info(
                "[DETECTED] language=%s from transcript=%r",
                raw_lang,
                transcript[:60],
            )
            if raw_lang == self._active_lang:
                self._pending_lang = None
                self._pending_lang_count = 0
                logger.debug(
                    "[HYSTERESIS] language=%s matches active, reset pending",
                    raw_lang,
                )
            elif raw_lang == self._pending_lang:
                self._pending_lang_count += 1
                logger.debug(
                    "[HYSTERESIS] language=%s matches pending count=%d",
                    raw_lang,
                    self._pending_lang_count,
                )
                if self._pending_lang_count >= 2:
                    self._active_lang = raw_lang
                    self._pending_lang = None
                    self._pending_lang_count = 0
                    logger.info(
                        "[LANGUAGE CONFIRMED] active_lang=%s confirmed_count=2",
                        raw_lang,
                    )
                    if self._language_state is not None:
                        self._language_state.set_language(raw_lang)
            else:
                self._pending_lang = raw_lang
                self._pending_lang_count = 1
                logger.debug(
                    "[HYSTERESIS] new pending language=%s count=1",
                    raw_lang,
                )

            # Voice swap follows the active (post-hysteresis) language.
            self._set_tts_voice(self._active_lang)

            # Resolve the ambient audio context (e.g. "Music" was playing)
            # once: it is both persisted with the transcript AND carried
            # on the outbound frame so the system-prompt injector can tell
            # the LLM what the room sounded like for THIS turn — the model
            # has no other sense of hearing.
            ae_label, ae_score = self._latest_audio_event()

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
                        audio_event_label=ae_label,
                        audio_event_score=ae_score,
                    )
                except Exception:
                    logger.exception(
                        "transcription_gate: failed to log transcript "
                        "(non-fatal)"
                    )

            # Push the (possibly coalesced) transcript downstream so the
            # LLM context aggregator can drive the turn. When override_text
            # was set by the debounce flush, mint a fresh frame carrying
            # the combined text — never mutate the inbound frame.
            if override_text is not None and transcript != (frame.text or ""):
                outbound = self._clone_with_text(frame, transcript)
            else:
                outbound = frame
            # Carry the current turn's ambient audio onto the frame so the
            # system-prompt injector can surface it as THIS turn's hearing
            # (not just buried in recent-transcript history).
            try:
                outbound.audio_event_label = ae_label
                outbound.audio_event_score = ae_score
            except Exception:
                logger.debug(
                    "transcription_gate: could not attach audio_event to "
                    "frame (non-fatal)"
                )
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

        def _latest_audio_event(
            self,
        ) -> tuple[str | None, float | None]:
            """Return ``(label, score)`` of the freshest confirmed audio
            event worth surfacing to the LLM, else ``(None, None)``.

            Forwards any label (Music, Speech, Applause, …) above
            ``_AUDIO_EVENT_FORWARD_MIN_SCORE`` as long as it fired within
            ``_AUDIO_EVENT_MAX_AGE_S`` of now, so a stale tag never bleeds
            onto a later utterance. Best-effort: any failure yields no tag.
            """
            if self.settings is None:
                return None, None
            try:
                from src.audio_event.reader import read_latest_audio_event

                result = read_latest_audio_event(
                    self.settings.audio_event_file
                )
            except Exception:
                logger.debug(
                    "transcription_gate: audio-event read failed "
                    "(non-fatal)",
                    exc_info=True,
                )
                return None, None
            if result is None:
                return None, None
            label, score, ts = result
            if score < _AUDIO_EVENT_FORWARD_MIN_SCORE:
                return None, None
            if time.time() - ts > _AUDIO_EVENT_MAX_AGE_S:
                return None, None
            return label, round(score, 3)

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
    bot_speech_state: Any = None,
):
    """Factory returning a TranscriptionGateProcessor instance."""
    cls = _build_transcription_gate_class()
    return cls(
        store=store,
        settings=settings,
        tts_service=tts_service,
        language_state=language_state,
        bot_speech_state=bot_speech_state,
    )


__all__ = [
    "create_transcription_gate",
]
