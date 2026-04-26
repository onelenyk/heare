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
from .language import (
    detect_script_language,
    is_standalone_cancel_imperative,
    voice_for_language,
)
from .openrouter_cli import OpenRouterCLI, OpenRouterError
from .zai_cli import ZaiCLI

if TYPE_CHECKING:
    from .actions import IntentQueue
    from .config import Settings
    from .storage import TranscriptStore


logger = logging.getLogger("heare.generator")

FALLBACK_PHRASE = "One moment, something went wrong."

_SENTENCE_TERMINATORS = ".!?…"

# Phase 2.2 US-P2.2-07 + Phase AH2-02: post-parser TTS scrubber.
# Defense-in-depth against tool-name / JSON fragment literals and
# Claude Code tool status markers reaching TTS.
_SCRUB_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Phase AH2-02: Claude Code's bash tool emits this literal marker when
    # a command has no stdout. Drop the whole phrase (parens included) so
    # TTS never says any fragment of it. Must run BEFORE the standalone
    # `bash` word-boundary rule below or only `Bash` would be stripped.
    (re.compile(r"\(\s*Bash completed with no output\s*\)", re.IGNORECASE), ""),
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


def _get_mcp_descriptions(settings: "Settings | None") -> str:
    """Get descriptions of MCP servers from workspace/.mcp.json.

    Returns formatted string describing available MCP tools, or empty string
    when no servers are configured. Called once at GeneratorProcessor init.
    """
    if settings is None:
        return ""

    from .mcp_utils import build_mcp_prompt_block, read_mcp_servers

    servers = read_mcp_servers(settings.workspace_dir)
    return build_mcp_prompt_block(servers)


def _load_pipecat_base():
    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        Frame,
        TTSSpeakFrame,
        TranscriptionFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    from .indication import IndicationCueFrame

    return (
        FrameProcessor,
        FrameDirection,
        Frame,
        TTSSpeakFrame,
        TranscriptionFrame,
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        IndicationCueFrame,
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
        IndicationCueFrame,
    ) = _load_pipecat_base()

    class GeneratorProcessor(FrameProcessor):  # type: ignore[misc,valid-type]
        def __init__(
            self,
            openrouter_cli: OpenRouterCLI | ZaiCLI,
            context_builder: ContextBuilder,
            prompt_template: str,
            persona: str,
            intent_queue: "IntentQueue",
            store: "TranscriptStore | None" = None,
            settings: "Settings | None" = None,
            conversation_manager: Any = None,  # ConversationManager | None; typed Any to avoid circular import
            tts_service: Any = None,
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
            self._tts_service = tts_service
            self._current_voice: str = settings.tts_voice if settings is not None else "en-US-AriaNeural"
            # Language state — populated fully by US-I18N-03; initialized here
            # so _set_tts_voice is safe to call even before hysteresis wiring.
            _default_lang = (
                settings.groq_language
                if settings is not None and settings.groq_language not in ("auto", "")
                else "en"
            )
            self._active_lang: str = _default_lang
            self._pending_lang: str | None = None
            self._pending_lang_count: int = 0
            # Feedback-loop guard — drop transcripts while bot speaks / cooldown
            self._bot_speaking = False
            self._bot_cooldown_until = 0.0
            # Indication-cue echo gate — set while a non-speech cue is playing
            # (mirrors _bot_speaking but scoped to cue-bracket window).
            self._indication_speaking = False
            self._bot_cooldown_seconds = (
                settings.bot_speaking_cooldown_seconds if settings is not None else 2.0
            )
            # Phase BP-02: STT debounce — coalesce TranscriptionFrames that
            # arrive close together (Groq STT mid-sentence pause splits one
            # utterance into two frames). 0 = legacy immediate dispatch.
            self._debounce_seconds: float = (
                settings.transcript_debounce_seconds if settings is not None else 0.0
            )
            self._debounce_buffer: list[str] = []
            self._debounce_frame: Any | None = None
            self._debounce_direction: Any | None = None
            import asyncio as _asyncio
            self._debounce_task: _asyncio.Task | None = None

            # Compute MCP descriptions once at init from workspace/.mcp.json.
            # No hot-reload — restart required when .mcp.json changes.
            self._mcp_descriptions: str = _get_mcp_descriptions(settings)

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)

            if isinstance(frame, BotStartedSpeakingFrame):
                self._bot_speaking = True
            elif isinstance(frame, BotStoppedSpeakingFrame):
                self._bot_speaking = False
                self._bot_cooldown_until = time.monotonic() + self._bot_cooldown_seconds
            elif isinstance(frame, IndicationCueFrame):
                self._indication_speaking = bool(frame.start)

            if isinstance(frame, TranscriptionFrame):
                if self._debounce_seconds > 0:
                    await self._schedule_transcription(frame, direction)
                else:
                    await self._handle_transcription(frame, direction)
            else:
                await self.push_frame(frame, direction)

        async def _schedule_transcription(self, frame: Any, direction: Any) -> None:
            """Buffer a TranscriptionFrame; debounce-fire after silence window."""
            import asyncio as _asyncio

            text = (frame.text or "").strip()
            if text:
                self._debounce_buffer.append(text)
            self._debounce_frame = frame
            self._debounce_direction = direction
            if self._debounce_task is not None and not self._debounce_task.done():
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
            await self._handle_transcription(frame, direction, override_text=combined)

        async def _push_tts(
            self, text: str, transcript_id: int | None = None
        ) -> bool:
            scrubbed = _scrub_tts_text(text)
            if not scrubbed:
                logger.warning(
                    "generator: TTS scrubber reduced text to empty, skipping push: %r",
                    text[:80],
                )
                return False
            await self.push_frame(TTSSpeakFrame(scrubbed))
            await self._log_spoken_decision(scrubbed, transcript_id)
            return True

        async def _log_spoken_decision(
            self, reply: str, transcript_id: int | None
        ) -> None:
            if self.store is None:
                return
            try:
                await self.store.log_decision(
                    transcript_id,
                    {"type": "speak", "reply": reply},
                )
            except Exception:
                logger.exception("generator: log_decision(speak) failed (non-fatal)")

        async def _submit_intent(
            self, payload: dict, transcript_id: int | None = None
        ) -> int | None:
            tool = str(payload.get("tool", "?"))
            args = str(payload.get("args", ""))
            decision_id: int | None = None
            if self.store is not None:
                try:
                    decision_id = await self.store.log_decision(
                        transcript_id,
                        {
                            "type": "act",
                            "intent": tool,
                            "action": {"tool": tool, "args": args},
                        },
                    )
                except Exception:
                    logger.exception("generator: log_decision(act) failed (non-fatal)")
            intent_id = await self.intent_queue.submit(
                payload,
                decision_id=decision_id,
                transcript_id=transcript_id,
                language=self._active_lang,
            )
            if intent_id is not None and self.conversation_manager is not None:
                self.conversation_manager.record_action_pending(
                    intent_id, tool, args
                )
            if intent_id is not None:
                logger.info(
                    "[INTENT SUBMITTED id=%d tool=%s]", intent_id, tool
                )
                from .indication import IndicationKind, get_indication

                ind = get_indication()
                if ind is not None:
                    ind.notify(
                        IndicationKind.INTENT_SUBMITTED, body=f"queued: {tool}"
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

        async def _handle_transcription(
            self,
            frame: Any,
            direction: Any,
            override_text: str | None = None,
        ) -> None:
            # override_text wins over frame.text when set; this is how the
            # debounce flush passes a coalesced multi-fragment string
            # without mutating the underlying frame (which may be of any
            # pipecat-version-specific dataclass shape).
            source_text = override_text if override_text is not None else (frame.text or "")
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
                    "generator: dropping transcript (bot=%s indication=%s enrollment=%s): %r",
                    self._bot_speaking,
                    self._indication_speaking,
                    enrollment_active,
                    transcript[:60],
                )
                return

            # Language detection + 2-turn hysteresis (US-I18N-03/05)
            from .language import detect_language_from_frame
            raw_lang = detect_language_from_frame(frame, fallback=self._active_lang)
            if raw_lang == self._active_lang:
                self._pending_lang = None
                self._pending_lang_count = 0
            elif raw_lang == self._pending_lang:
                self._pending_lang_count += 1
                if self._pending_lang_count >= 2:
                    self._active_lang = raw_lang
                    self._pending_lang = None
                    self._pending_lang_count = 0
            else:
                self._pending_lang = raw_lang
                self._pending_lang_count = 1

            # Swap TTS voice to match active language (US-I18N-05)
            self._set_tts_voice(self._active_lang)

            # Persist transcript so the watch dashboard sees user activity.
            # Capture the id so Phase B-0 can correlate decisions/actions.
            transcript_id: int | None = None
            if self.store is not None and self.settings is not None:
                try:
                    transcript_id = await self.store.log_transcript(
                        transcript,
                        self.settings.mode.value,
                        speaker_id=getattr(frame, "speaker_id", None),
                        speaker_confidence=getattr(frame, "speaker_confidence", None),
                    )
                except Exception:
                    logger.exception("generator: failed to log transcript (non-fatal)")

            # Cancel keyword gate — full cancel (queued + in-flight)
            # US-WU-02: smart standalone-imperative detector promoted from
            # the dormant decider. Negation guard, ≤4-word residual,
            # context-token guard. EC-2 of the consensus plan: no escape
            # hatch — the old keyword-list regex approach is gone.
            cancelled_anything = False
            stop_words = (
                self.settings.cancel_stop_words
                if self.settings is not None
                else ["stop", "cancel", "halt", "відміни", "отмени", "стоп"]
            )
            if is_standalone_cancel_imperative(transcript, stop_words):
                cancelled_anything = await self.intent_queue.cancel_active()
                if cancelled_anything:
                    logger.info(
                        "[CANCEL FAST-PATH transcript=%r]", transcript[:80]
                    )
                # INTENT_CANCELLED indication is fired inside cancel_active() at
                # actions.py:233 — do NOT also fire it here. Single emit site
                # required by EC-1 of the consensus plan.

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
            parser = IntentStreamParser(
                max_intents=(
                    self.settings.max_intents_per_response
                    if self.settings
                    else 10
                )
            )
            try:
                ctx = await self.context_builder.build_for_generator(
                    transcript=transcript,
                    persona=self.persona,
                    conversation_id=conversation_id,
                    user_language=self._active_lang,
                )
                # MCP pre-check: inject enabled MCP descriptions to avoid LLM rejection
                # Uses cached descriptions that auto-reload on config change
                if self._mcp_descriptions:
                    ctx["mcp_servers"] = self._mcp_descriptions
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
                        if text and await self._push_tts(text, transcript_id):
                            full_text_parts.append(text)
                            chunk_count += 1
                        buffer = remainder
                    for intent_payload in intents:
                        if await self._submit_intent(
                            intent_payload, transcript_id
                        ) is not None:
                            intent_count += 1
                speech, intents = parser.flush()
                buffer += speech
                for intent_payload in intents:
                    if await self._submit_intent(
                        intent_payload, transcript_id
                    ) is not None:
                        intent_count += 1
                tail = buffer.strip()
                if tail and await self._push_tts(tail, transcript_id):
                    full_text_parts.append(tail)
                    chunk_count += 1
            except OpenRouterError as e:
                logger.warning("generator: OpenRouter failed — %s; pushing fallback", e)
                from .indication import IndicationKind, get_indication

                ind = get_indication()
                if ind is not None:
                    ind.notify(IndicationKind.OPENROUTER_TIMEOUT, body=str(e)[:160])
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
                '[TIMING] generator transcript="%s" lang=%s ttft=%dms chunks=%d intents=%d cancelled=%s',
                transcript[:80],
                self._active_lang,
                int(ttft_display),
                chunk_count,
                intent_count,
                "yes" if cancelled_anything else "none",
            )

            # Gemini response language compliance logging (US-I18N-03)
            if full_text_parts:
                reply_text = " ".join(full_text_parts)
                if len(reply_text) > 5:
                    detected_script = detect_script_language(reply_text)
                    expected_script = "cyrillic" if self._active_lang in ("uk", "ru") else "latin"
                    if detected_script != expected_script:
                        logger.warning(
                            '[LANG_MISMATCH] expected=%s detected_script=%s transcript="%s" reply="%s"',
                            self._active_lang,
                            detected_script,
                            transcript[:80],
                            reply_text[:80],
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
                "[TTS VOICE SWAP] from=%s to=%s lang=%s", old_voice, new_voice, lang
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
    openrouter_cli: OpenRouterCLI | ZaiCLI,
    context_builder: ContextBuilder,
    prompt_template: str,
    persona: str,
    store: "TranscriptStore | None" = None,
    settings: "Settings | None" = None,
    intent_queue: "IntentQueue | None" = None,
    conversation_manager: Any = None,
    tts_service: Any = None,
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
        tts_service,
    )


__all__ = [
    "FALLBACK_PHRASE",
    "create_generator_processor",
]
