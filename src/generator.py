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
        ) -> None:
            super().__init__()
            self.openrouter_cli = openrouter_cli
            self.context_builder = context_builder
            self.prompt_template = prompt_template
            self.persona = persona
            self.intent_queue = intent_queue
            self.store = store
            self.settings = settings
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

            t_start = time.monotonic()
            ttft_ms: float | None = None
            chunk_count = 0
            intent_count = 0
            buffer = ""
            full_text_parts: list[str] = []
            parser = IntentStreamParser()
            try:
                ctx = await self.context_builder.build_for_generator(
                    transcript=transcript, persona=self.persona
                )
                prompt = self.context_builder.render(self.prompt_template, ctx)
                async for chunk in self.openrouter_cli.generate(prompt):
                    if not chunk:
                        continue
                    if ttft_ms is None:
                        ttft_ms = (time.monotonic() - t_start) * 1000
                    # Route chunk through the parser — separates TTS text
                    # from intent payloads with anti-leakage invariants.
                    speech, intents = parser.feed(chunk)
                    buffer += speech
                    sentence, remainder = _split_on_sentence(buffer)
                    if sentence:
                        text = sentence.strip()
                        if text:
                            await self.push_frame(TTSSpeakFrame(text))
                            full_text_parts.append(text)
                            chunk_count += 1
                        buffer = remainder
                    for intent_payload in intents:
                        intent_id = await self.intent_queue.submit(intent_payload)
                        if intent_id is not None:
                            intent_count += 1
                            logger.info(
                                "[INTENT SUBMITTED id=%d tool=%s]",
                                intent_id,
                                intent_payload.get("tool", "?"),
                            )
                # End-of-stream: release any held parser bytes + flush tail
                speech, intents = parser.flush()
                buffer += speech
                for intent_payload in intents:
                    intent_id = await self.intent_queue.submit(intent_payload)
                    if intent_id is not None:
                        intent_count += 1
                        logger.info(
                            "[INTENT SUBMITTED id=%d tool=%s]",
                            intent_id,
                            intent_payload.get("tool", "?"),
                        )
                tail = buffer.strip()
                if tail:
                    await self.push_frame(TTSSpeakFrame(tail))
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
    )


__all__ = [
    "FALLBACK_PHRASE",
    "create_generator_processor",
]
