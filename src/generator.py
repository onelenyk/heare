"""GeneratorProcessor — always-replies pipeline stage for s2s-realtime.

Phase 2.1: streams LLM chunks through IntentStreamParser, speaks the
non-intent text via TTSSpeakFrame, submits intents to IntentQueue,
and honors a minimal cancel keyword gate for "скасуй"/"відміни".

Pipecat imports are deferred so admin CLI paths work on machines
without portaudio.
"""
from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Any

from .context import ContextBuilder
from .intent_parser import IntentStreamParser
from .openrouter_cli import OpenRouterCLI, OpenRouterError

if TYPE_CHECKING:
    from .actions import IntentQueue
    from .config import Settings
    from .storage import TranscriptStore


logger = logging.getLogger("heare.generator")

FALLBACK_PHRASE = "Хвилинку, щось не так."

_SENTENCE_TERMINATORS = ".!?…"

# Cancel keyword gate. "стоп" intentionally excluded — too many false
# positives ("стоп-кадр", "автостоп"). The boundary class rejects
# substring matches like "скаси" (different stem).
_CANCEL_RE = re.compile(
    r"(?i)(?:^|[\s.,!?—])(скасуй|відміни)(?:$|[\s.,!?—])"
)

# Phase 2.2 US-P2.2-07: post-parser TTS scrubber. Defense-in-depth
# against tool-name / JSON fragment literals reaching TTS if Gemini
# temporarily ignores the prompt rule. Strips or neutralizes.
_SCRUB_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Drop standalone bash/Bash token (word boundary) — but NOT "bashful"
    (re.compile(r"(?<![\w])bash(?![\w])", re.IGNORECASE), ""),
    # JSON fragments that clearly leaked from an intent tag
    (re.compile(r'\{"tool"\s*:\s*"[^"]*"\s*,\s*"args"\s*:\s*"[^"]*"\s*\}'), ""),
    (re.compile(r'"tool"\s*:\s*"[^"]*"'), ""),
    (re.compile(r'"args"\s*:\s*"[^"]*"'), ""),
    (re.compile(r"</?\s*intent\s*>", re.IGNORECASE), ""),
    # Cleanup: collapse 2+ spaces left behind, trim
    (re.compile(r"\s{2,}"), " "),
]


def _scrub_tts_text(text: str) -> str:
    """Strip tool-name literals and JSON fragments before TTS synthesis."""
    out = text
    for pat, repl in _SCRUB_PATTERNS:
        out = pat.sub(repl, out)
    return out.strip()


def _split_on_sentence(buffer: str) -> tuple[str, str]:
    """Return (complete_sentence_prefix, remainder)."""
    last = -1
    for i, ch in enumerate(buffer):
        if ch in _SENTENCE_TERMINATORS:
            last = i
    if last < 0:
        return "", buffer
    return buffer[: last + 1], buffer[last + 1 :]


def _load_pipecat_base():
    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        Frame,
        TTSSpeakFrame,
        TranscriptionFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    return (
        FrameProcessor,
        FrameDirection,
        Frame,
        TTSSpeakFrame,
        TranscriptionFrame,
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
    )


_generator_cls: type | None = None


def _build_generator_processor_class():
    global _generator_cls
    if _generator_cls is not None:
        return _generator_cls
    (
        FrameProcessor,
        FrameDirection,
        Frame,
        TTSSpeakFrame,
        TranscriptionFrame,
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
    ) = _load_pipecat_base()

    class GeneratorProcessor(FrameProcessor):  # type: ignore[misc,valid-type]
        def __init__(
            self,
            openrouter_cli: OpenRouterCLI,
            context_builder: ContextBuilder,
            prompt_template: str,
            persona: str,
            intent_queue: "IntentQueue",
            store: "TranscriptStore | None" = None,
            settings: "Settings | None" = None,
            conversation_manager: Any = None,  # ConversationManager | None; typed Any to avoid circular import
        ) -> None:
            super().__init__()
            self.openrouter_cli = openrouter_cli
            self.context_builder = context_builder
            self.prompt_template = prompt_template
            self.persona = persona
            self.intent_queue = intent_queue
            self.store = store
            self.settings = settings
            self.conversation_manager = conversation_manager
            # Feedback-loop guard — drop transcripts while bot speaks / cooldown
            self._bot_speaking = False
            self._bot_cooldown_until = 0.0
            self._bot_cooldown_seconds = (
                settings.bot_speaking_cooldown_seconds if settings is not None else 2.0
            )

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)

            if isinstance(frame, BotStartedSpeakingFrame):
                self._bot_speaking = True
            elif isinstance(frame, BotStoppedSpeakingFrame):
                self._bot_speaking = False
                self._bot_cooldown_until = time.monotonic() + self._bot_cooldown_seconds

            if isinstance(frame, TranscriptionFrame):
                await self._handle_transcription(frame, direction)
            else:
                await self.push_frame(frame, direction)

        async def _push_tts(self, text: str) -> bool:
            scrubbed = _scrub_tts_text(text)
            if not scrubbed:
                logger.warning(
                    "generator: TTS scrubber reduced text to empty, skipping push: %r",
                    text[:80],
                )
                return False
            await self.push_frame(TTSSpeakFrame(scrubbed))
            return True

        async def _submit_intent(self, payload: dict) -> int | None:
            intent_id = await self.intent_queue.submit(payload)
            if intent_id is not None and self.conversation_manager is not None:
                self.conversation_manager.record_action_pending(
                    intent_id,
                    str(payload.get("tool", "?")),
                    str(payload.get("args", "")),
                )
            if intent_id is not None:
                logger.info(
                    "[INTENT SUBMITTED id=%d tool=%s]",
                    intent_id,
                    payload.get("tool", "?"),
                )
            return intent_id

        async def _background_memory_update(
            self, transcript: str, reply_text: str, conversation_id: int
        ) -> None:
            try:
                topics: list[str] = []
                if self.conversation_manager is None:
                    return
                if self.settings is not None and getattr(
                    self.settings, "topic_extraction_enabled", True
                ):
                    try:
                        topics = await self.conversation_manager.extract_topics(reply_text)
                    except Exception:
                        logger.exception("generator: extract_topics failed (non-fatal)")
                turn_text = f"{transcript} {reply_text}".strip()
                await self.conversation_manager.update_summary(
                    conversation_id, turn_text, topics
                )
                logger.info(
                    "[MEMORY UPDATE conv=%s topics=%d turn_len=%d]",
                    conversation_id,
                    len(topics),
                    len(turn_text),
                )
            except Exception:
                logger.exception("generator: background memory update failed (non-fatal)")

        async def _handle_transcription(self, frame: Any, direction: Any) -> None:
            transcript = (frame.text or "").strip()
            if not transcript:
                return

            if self._bot_speaking or time.monotonic() < self._bot_cooldown_until:
                logger.debug(
                    "generator: dropping transcript while bot speaking/cooldown: %r",
                    transcript[:60],
                )
                return

            # Persist transcript so the watch dashboard sees user activity
            if self.store is not None and self.settings is not None:
                try:
                    await self.store.log_transcript(
                        transcript,
                        self.settings.mode.value,
                        speaker_id=getattr(frame, "speaker_id", None),
                        speaker_confidence=getattr(frame, "speaker_confidence", None),
                    )
                except Exception:
                    logger.exception("generator: failed to log transcript (non-fatal)")

            # Cancel keyword gate — pending-only cancellation
            cancelled_id: int | None = None
            if _CANCEL_RE.search(transcript):
                cancelled = self.intent_queue.cancel_latest()
                if cancelled is not None:
                    cancelled_id = cancelled.id
                    logger.info("[INTENT CANCELLED id=%d]", cancelled.id)

            # Acquire conversation_id once per turn (Phase 2.2 US-P2.2-02)
            conversation_id: int | None = None
            if self.conversation_manager is not None:
                try:
                    conversation_id = await self.conversation_manager.get_or_create_active()
                except Exception:
                    logger.exception("generator: get_or_create_active failed (non-fatal)")

            t_start = time.monotonic()
            ttft_ms: float | None = None
            chunk_count = 0
            intent_count = 0
            buffer = ""
            full_text_parts: list[str] = []
            parser = IntentStreamParser()
            try:
                ctx = await self.context_builder.build_for_generator(
                    transcript=transcript,
                    persona=self.persona,
                    conversation_id=conversation_id,
                )
                prompt = self.context_builder.render(self.prompt_template, ctx)
                async for chunk in self.openrouter_cli.generate(prompt):
                    if not chunk:
                        continue
                    if ttft_ms is None:
                        ttft_ms = (time.monotonic() - t_start) * 1000
                    speech, intents = parser.feed(chunk)
                    buffer += speech
                    sentence, remainder = _split_on_sentence(buffer)
                    if sentence:
                        text = sentence.strip()
                        if text and await self._push_tts(text):
                            full_text_parts.append(text)
                            chunk_count += 1
                        buffer = remainder
                    for intent_payload in intents:
                        if await self._submit_intent(intent_payload) is not None:
                            intent_count += 1
                speech, intents = parser.flush()
                buffer += speech
                for intent_payload in intents:
                    if await self._submit_intent(intent_payload) is not None:
                        intent_count += 1
                tail = buffer.strip()
                if tail and await self._push_tts(tail):
                    full_text_parts.append(tail)
                    chunk_count += 1
            except OpenRouterError as e:
                logger.warning("generator: OpenRouter failed — %s; pushing fallback", e)
                await self.push_frame(TTSSpeakFrame(FALLBACK_PHRASE))
                chunk_count = 1
            except Exception:
                # asyncio.CancelledError inherits from BaseException on 3.8+,
                # so this Exception catch won't silence cooperative cancel.
                logger.exception("generator: unexpected failure; pushing fallback")
                await self.push_frame(TTSSpeakFrame(FALLBACK_PHRASE))
                chunk_count = 1

            if chunk_count == 0:
                logger.warning(
                    "generator: empty reply from OpenRouter for transcript=%r",
                    transcript[:80],
                )

            total_ms = (time.monotonic() - t_start) * 1000
            ttft_display = ttft_ms if ttft_ms is not None else total_ms
            logger.info(
                '[TIMING] generator transcript="%s" ttft=%dms chunks=%d intents=%d cancelled=%s',
                transcript[:80],
                int(ttft_display),
                chunk_count,
                intent_count,
                cancelled_id if cancelled_id is not None else "none",
            )

            # Phase 2.2 US-P2.2-02: background memory update — never awaited
            # inside this method so the turn is "done" once TTS frames are
            # pushed. Exceptions caught inside the coroutine.
            if (
                self.conversation_manager is not None
                and conversation_id is not None
                and full_text_parts
            ):
                reply_text = " ".join(full_text_parts)
                import asyncio as _asyncio  # local to avoid top-level pollution
                _asyncio.create_task(
                    self._background_memory_update(transcript, reply_text, conversation_id)
                )

        async def shutdown(self) -> None:
            """No-op: parity with DeciderProcessor.shutdown() for main teardown."""
            return

        async def on_heartbeat_tick(self) -> None:
            """No-op: parity with DeciderProcessor for HeartbeatTask compatibility."""
            return

    _generator_cls = GeneratorProcessor
    return _generator_cls


def create_generator_processor(
    openrouter_cli: OpenRouterCLI,
    context_builder: ContextBuilder,
    prompt_template: str,
    persona: str,
    store: "TranscriptStore | None" = None,
    settings: "Settings | None" = None,
    intent_queue: "IntentQueue | None" = None,
    conversation_manager: Any = None,
):
    """Build a GeneratorProcessor. `intent_queue` defaults to a fresh
    IntentQueue so tests don't need to pre-instantiate one. Production
    callers (main.py) always pass the daemon-lifetime queue.
    """
    from .actions import IntentQueue as _IQ

    if intent_queue is None:
        intent_queue = _IQ()
    cls = _build_generator_processor_class()
    return cls(
        openrouter_cli,
        context_builder,
        prompt_template,
        persona,
        intent_queue,
        store,
        settings,
        conversation_manager,
    )


__all__ = [
    "FALLBACK_PHRASE",
    "create_generator_processor",
]
