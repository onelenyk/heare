"""Phase-1 GeneratorProcessor.

Replaces DeciderProcessor for the s2s-realtime branch. Always produces a
reply — no classification, no nothing/speak/act branching. Streams reply
chunks into TTS as they arrive from OpenRouter.

Pipecat imports are deferred so admin CLI paths work on machines without
portaudio.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from .context import ContextBuilder
from .decider import FIXED_PHRASES
from .openrouter_cli import OpenRouterCLI, OpenRouterError

if TYPE_CHECKING:
    from .config import Settings
    from .storage import TranscriptStore


logger = logging.getLogger("heare.generator")

FALLBACK_PHRASE = "Хвилинку, щось не так."

_SENTENCE_TERMINATORS = ".!?…"


def _split_on_sentence(buffer: str) -> tuple[str, str]:
    """Return (complete_sentence_prefix, remainder).

    Scans `buffer` for the last sentence terminator. If found, splits so the
    first element contains a complete sentence (or run of sentences) and the
    second contains everything after. Otherwise returns ('', buffer).
    """
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
            store: "TranscriptStore | None" = None,
            settings: "Settings | None" = None,
        ) -> None:
            super().__init__()
            self.openrouter_cli = openrouter_cli
            self.context_builder = context_builder
            self.prompt_template = prompt_template
            self.persona = persona
            self.store = store
            self.settings = settings
            # Feedback-loop guard: drop transcripts heard while bot is
            # speaking (or shortly after — STT has audio in flight).
            self._bot_speaking = False
            self._bot_cooldown_until = 0.0
            self._bot_cooldown_seconds = (
                settings.bot_speaking_cooldown_seconds if settings is not None else 2.0
            )

        async def process_frame(self, frame: Any, direction: Any) -> None:
            # Pipecat requires super().process_frame first for internal
            # bookkeeping (start/stop state tracking).
            await super().process_frame(frame, direction)

            # Bot-speaking state — drives the feedback-loop guard in
            # _handle_transcription. We still forward these frames so
            # downstream services see them.
            if isinstance(frame, BotStartedSpeakingFrame):
                self._bot_speaking = True
            elif isinstance(frame, BotStoppedSpeakingFrame):
                self._bot_speaking = False
                self._bot_cooldown_until = time.monotonic() + self._bot_cooldown_seconds

            if isinstance(frame, TranscriptionFrame):
                await self._handle_transcription(frame, direction)
            else:
                # Forward EVERYTHING else downstream so EdgeTTS sees
                # StartFrame / EndFrame / audio-control frames. Without
                # this, downstream services never initialize and all
                # TTSSpeakFrames we push later are silently dropped.
                await self.push_frame(frame, direction)

        async def _handle_transcription(self, frame: Any, direction: Any) -> None:
            transcript = (frame.text or "").strip()
            if not transcript:
                return

            # Feedback-loop guard: drop transcripts while bot is speaking
            # OR during the post-speech cooldown window (STT may still be
            # processing tail of bot audio).
            if self._bot_speaking or time.monotonic() < self._bot_cooldown_until:
                logger.debug(
                    "generator: dropping transcript while bot speaking/cooldown: %r",
                    transcript[:60],
                )
                return

            # Persist the incoming transcript so the watch dashboard / storage
            # reflects user activity (parity with legacy DeciderProcessor).
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

            t_start = time.monotonic()
            ttft_ms: float | None = None
            chunk_count = 0
            buffer = ""
            full_text_parts: list[str] = []
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
                    buffer += chunk
                    # Flush on sentence boundary so EdgeTTS gets coherent text.
                    sentence, remainder = _split_on_sentence(buffer)
                    if sentence:
                        text = sentence.strip()
                        if text:
                            await self.push_frame(TTSSpeakFrame(text))
                            full_text_parts.append(text)
                            chunk_count += 1
                        buffer = remainder
                # Flush any trailing partial sentence
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
            reply_preview = " ".join(full_text_parts)[:100]
            logger.info(
                '[TIMING] generator transcript="%s" ttft=%dms total_chunks=%d reply=%r',
                transcript[:80],
                int(ttft_display),
                chunk_count,
                reply_preview,
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
):
    cls = _build_generator_processor_class()
    return cls(openrouter_cli, context_builder, prompt_template, persona, store, settings)


__all__ = [
    "FALLBACK_PHRASE",
    "FIXED_PHRASES",
    "create_generator_processor",
]
