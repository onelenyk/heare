"""Stateful frame processor: LISTENING → AWAITING_CONFIRMATION → EXECUTING.

Pipecat imports are deferred so tests and `--help` work without portaudio.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from .config import DeciderState, Mode, Settings
from .storage import EventKind

if TYPE_CHECKING:
    from .claude_cli import ClaudeCLI
    from .context import ContextBuilder
    from .storage import TranscriptStore


# Size rationale: worst-case 10s action with ~20 stdout lines + ~12 state
# transitions = ~32 events, 256 = 8x headroom. If a user runs a 30s streaming
# action that produces 500 stdout lines, some will drop and the drop counter
# surfaces via system.emit_drops in the heartbeat loop.
_EMIT_QUEUE_SIZE = 256


logger = logging.getLogger("heare.decider")


def _load_pipecat_base():
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        Frame,
        TTSSpeakFrame,
        TranscriptionFrame,
        UserStartedSpeakingFrame,
        UserStoppedSpeakingFrame,
    )

    return (
        FrameProcessor,
        FrameDirection,
        Frame,
        TTSSpeakFrame,
        TranscriptionFrame,
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        UserStartedSpeakingFrame,
        UserStoppedSpeakingFrame,
    )


_FILLER_TOKEN = r"(хм+|ну+|пу+|ах+|ех+|ум+|ой+|ай+|м+|а+|о+|е+|у+|и+|ь+)"
NOISE_PATTERN = re.compile(
    rf"^({_FILLER_TOKEN}[\-\s,]*)+[\.,!\?\s]*$",
    re.IGNORECASE,
)

# Fixed phrases the assistant says often — pre-renderable into TTSCache so
# they play instantly instead of round-tripping through edge-tts.
FIXED_PHRASES: list[str] = [
    "okay",
    "nevermind, cancelled",
    "Скажи: так чи ні?",
    "дія не вдалася",
]

# Wake words that signal "user is talking TO Heare"
WAKE_WORD_PATTERN = re.compile(r"\b(гава|heare|гей)\b", re.IGNORECASE)

# Other people the user might address (NOT Heare). Detected as standalone words
# so words like "мама" (mum) are caught but "мамонт" (mammoth) is not.
OTHER_PERSON_PATTERN = re.compile(
    r"\b(гала|мамо|мама|мам|тато|тат|алло|alyona|alex)\b",
    re.IGNORECASE,
)

# Ukrainian question words (matched on word boundaries)
UA_QUESTION_WORD_PATTERN = re.compile(
    r"\b(чи|як|що|коли|чому|хто|де|навіщо|скільки)\b",
    re.IGNORECASE,
)

# Cyrillic script range covering Ukrainian letters
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def is_noise(text: str) -> bool:
    """Detect filler/noise transcripts that don't warrant a decider call."""
    cleaned = text.strip()
    if not cleaned:
        return True
    if not any(c.isalpha() for c in cleaned):
        return True
    return bool(NOISE_PATTERN.match(cleaned))


def _is_mostly_non_ukrainian(text: str) -> bool:
    """True if less than 30% of alphabetic characters are Cyrillic.

    Empty / no-alpha strings return False (they're handled by is_noise).
    """
    cyrillic = len(_CYRILLIC_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    alpha_total = cyrillic + latin
    if alpha_total == 0:
        return False
    return (cyrillic / alpha_total) < 0.30


def _looks_like_question(text: str) -> bool:
    """True if text ends with '?' or contains a Ukrainian question word."""
    if text.rstrip().endswith("?"):
        return True
    return bool(UA_QUESTION_WORD_PATTERN.search(text))


def is_quick_nothing(transcript: str, mode: Mode) -> bool:
    """Decide locally (no LLM) when a transcript is clearly NOT for Heare.

    Filter ordering (first match wins):
      RULE 0: Wake-word ALWAYS bypasses all other rules
      RULE 1: Focus mode without wake-word → True
      RULE 2: Other-person address → True
      RULE 3: Short transcript (< 3 words) in ambient → True
      RULE 4: Mostly non-Ukrainian in ambient → True
      RULE 5: Declarative (no question marker) in ambient → True
    """
    cleaned = transcript.strip()
    if not cleaned:
        return False  # is_noise handles empty
    # RULE 0: wake-word bypass — runs first so "heare status" passes even
    # though it's Latin-only and short.
    if WAKE_WORD_PATTERN.search(cleaned):
        return False
    # RULE 1: focus mode with no wake-word
    if mode == Mode.FOCUS:
        return True
    # RULE 2: other-person address in any mode
    if OTHER_PERSON_PATTERN.search(cleaned):
        return True
    # RULES 3-5 only apply in ambient mode
    if mode != Mode.AMBIENT:
        return False
    # RULE 3: too short to carry intent
    if len(cleaned.split()) < 3:
        return True
    # RULE 4: not a language we speak
    if _is_mostly_non_ukrainian(cleaned):
        return True
    # RULE 5: declarative statement, not directed at us
    if not _looks_like_question(cleaned):
        return True
    return False


YES_PATTERNS = [
    r"\bтак\b",
    r"\bда\b",
    r"\bага\b",
    r"\bокей\b",
    r"\bok\b",
    r"\byes\b",
    r"\byeah\b",
    r"\bsure\b",
    r"\bgo\b",
    r"\bдавай\b",
    r"\bзроби\b",
    r"\bвперед\b",
    r"\bконечно\b",
    r"\bкрасава\b",
    r"\bчому ні\b",
]

NO_PATTERNS = [
    r"\bні\b",
    r"\bнет\b",
    r"\bне треба\b",
    r"\bне потрібно\b",
    r"\bnevermind\b",
    r"\bcancel\b",
    r"\bstop\b",
    r"\bskip\b",
    r"\bно\b",
    r"\babort\b",
    r"\bне\b",
    r"\bне зараз\b",
]


def parse_yes_no(text: str) -> str:
    lowered = text.strip().lower()
    if not lowered:
        return "unclear"
    for pat in YES_PATTERNS:
        if re.search(pat, lowered):
            return "yes"
    for pat in NO_PATTERNS:
        if re.search(pat, lowered):
            return "no"
    return "unclear"


_decider_cls = None


def _build_decider_processor_class():
    global _decider_cls
    if _decider_cls is not None:
        return _decider_cls
    (
        FrameProcessor,
        FrameDirection,
        Frame,
        TTSSpeakFrame,
        TranscriptionFrame,
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        UserStartedSpeakingFrame,
        UserStoppedSpeakingFrame,
    ) = _load_pipecat_base()

    class DeciderProcessor(FrameProcessor):  # type: ignore[misc,valid-type]
        def __init__(
            self,
            claude_cli: "ClaudeCLI",
            store: "TranscriptStore",
            context_builder: "ContextBuilder",
            settings: Settings,
            decider_prompt_template: str,
        ) -> None:
            super().__init__()
            self.claude_cli = claude_cli
            self.store = store
            self.context_builder = context_builder
            self.settings = settings
            self.decider_prompt_template = decider_prompt_template
            self.state: DeciderState = DeciderState.LISTENING
            self.pending_action: dict[str, Any] | None = None
            self.pending_decision_id: int | None = None
            self.pending_speaker_id: str | None = None
            self.confirmation_deadline: float | None = None
            self._last_transcript: str | None = None
            self._lock = asyncio.Lock()
            self._timeout_task: asyncio.Task | None = None
            self._bot_speaking = False
            self._bot_cooldown_task: asyncio.Task | None = None
            # RT-002: fire-and-forget emit queue for progress events.
            # put_nowait is sync so emitting inside `async with self._lock`
            # never yields and never blocks the FSM critical path.
            self._emit_queue: asyncio.Queue[
                tuple[str, int | None, int | None, dict | None]
            ] = asyncio.Queue(maxsize=_EMIT_QUEUE_SIZE)
            self._emit_drop_count: int = 0
            self._emit_drainer_task: asyncio.Task | None = None
            # LAT-B4: speculative context pre-built on UserStartedSpeakingFrame
            self._speculative_prompt: str | None = None
            self._speculative_ctx: dict | None = None
            self._speculative_task: asyncio.Task | None = None
            self._speculative_started_at: float | None = None
            self._speculative_stale_after_seconds: float = 5.0

        def _begin_speculative_context(self) -> None:
            """Kick off async context + prompt build while user is still speaking."""
            # Cancel any in-flight speculation from a prior utterance
            self._clear_speculative()
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            self._speculative_started_at = time.monotonic()
            self._speculative_task = loop.create_task(self._build_speculative())

        async def _build_speculative(self) -> None:
            try:
                # Keep BOTH placeholders literal during speculative render so
                # we can substitute the real transcript AND the real speaker
                # rule block at execution time without re-rendering the whole
                # template. See plan §4 / speculative prompt double-sub.
                ctx = await self.context_builder.build(
                    transcript="{transcript_or_heartbeat}",
                    heartbeat=False,
                    keep_placeholders=[
                        "transcript_or_heartbeat",
                        "speaker_rule_block",
                    ],
                )
                prompt_template = self.context_builder.render(
                    self.decider_prompt_template, ctx
                )
                self._speculative_ctx = ctx
                self._speculative_prompt = prompt_template
            except Exception as e:
                logger.warning("speculative context build failed: %s", e)
                self._speculative_ctx = None
                self._speculative_prompt = None

        def _is_speculative_stale(self) -> bool:
            if self._speculative_started_at is None:
                return True
            return (
                time.monotonic() - self._speculative_started_at
                > self._speculative_stale_after_seconds
            )

        def _clear_speculative(self) -> None:
            task = self._speculative_task
            self._speculative_task = None
            if task is not None and not task.done():
                task.cancel()
            self._speculative_ctx = None
            self._speculative_prompt = None
            self._speculative_started_at = None

        # RT-002: fire-and-forget progress-event emission
        def _safe_emit(
            self,
            kind: str | EventKind,
            *,
            transcript_id: int | None = None,
            decision_id: int | None = None,
            payload: dict | None = None,
        ) -> None:
            """Enqueue a progress event. Never raises, never blocks.

            Called from inside the FSM lock — put_nowait is sync, so the lock
            holder yields zero extra time. Overflow drops the event and logs
            a WARNING once per 10 drops so disk contention never breaks the
            voice-confirmation critical path.
            """
            self._ensure_drainer()
            try:
                self._emit_queue.put_nowait(
                    (str(kind), transcript_id, decision_id, payload)
                )
            except asyncio.QueueFull:
                self._emit_drop_count += 1
                if self._emit_drop_count % 10 == 1:
                    logger.warning(
                        "progress event queue full (drops=%d) — dropping %s",
                        self._emit_drop_count,
                        kind,
                    )

        def _ensure_drainer(self) -> None:
            if self._emit_drainer_task is not None and not self._emit_drainer_task.done():
                return
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            self._emit_drainer_task = loop.create_task(self._drain_events())

        async def _drain_events(self) -> None:
            """Long-lived drainer. Never dies: any store exception is logged
            and the loop continues to the next event."""
            while True:
                try:
                    kind, tid, did, payload = await self._emit_queue.get()
                except asyncio.CancelledError:
                    return
                try:
                    await self.store.log_event(
                        kind,
                        transcript_id=tid,
                        decision_id=did,
                        payload=payload,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    logger.warning("progress event drainer: log_event failed: %s", e)

        async def shutdown(self) -> None:
            """Cancel the drainer and drain any remaining items with a
            100ms budget. Call from pipeline teardown."""
            task = self._emit_drainer_task
            self._emit_drainer_task = None
            if task is not None and not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=0.1)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

        async def _prompt_for_transcript(self, transcript: str) -> str:
            """Build the final decider prompt for a transcript.

            LAT-B4: if speculative context is available and not stale, reuse it
            by substituting {transcript_or_heartbeat}.
            SPK-005: also substitute {speaker_rule_block} with the real rule
            block computed from the current flag state.
            """
            if (
                self._speculative_task is not None
                and not self._speculative_task.done()
            ):
                # Speculation still building — wait up to 200ms for it, then
                # fall back to normal build if it's too slow.
                try:
                    await asyncio.wait_for(
                        asyncio.shield(self._speculative_task), timeout=0.2
                    )
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
            if (
                self._speculative_prompt is not None
                and not self._is_speculative_stale()
            ):
                rule_block = self.context_builder._render_rule_block()
                prompt = self._speculative_prompt.replace(
                    "{transcript_or_heartbeat}", transcript, 1
                ).replace(
                    "{speaker_rule_block}", rule_block, 1
                )
                self._clear_speculative()
                return prompt
            # Fallback: build from scratch
            self._clear_speculative()
            ctx = await self.context_builder.build(transcript, heartbeat=False)
            return self.context_builder.render(self.decider_prompt_template, ctx)

        def _reload_mode(self) -> None:
            mode_file = self.settings.mode_file
            if not mode_file.exists():
                return
            try:
                raw = mode_file.read_text().strip()
                if raw:
                    self.settings.mode = Mode(raw)
            except (OSError, ValueError) as e:
                logger.warning("failed to reload mode from %s: %s", mode_file, e)

        async def process_frame(self, frame, direction) -> None:  # type: ignore[override]
            await super().process_frame(frame, direction)

            if isinstance(frame, BotStartedSpeakingFrame):
                self._bot_speaking = True
                self._cancel_bot_cooldown()
                await self.push_frame(frame, direction)
                return
            if isinstance(frame, BotStoppedSpeakingFrame):
                self._schedule_bot_cooldown()
                await self.push_frame(frame, direction)
                return
            if isinstance(frame, UserStartedSpeakingFrame):
                # LAT-B4: pre-build prompt context while user is still speaking
                self._begin_speculative_context()
                await self.push_frame(frame, direction)
                return

            transcript = self._extract_transcript(frame)
            if transcript is None:
                await self.push_frame(frame, direction)
                return

            if self._bot_speaking:
                logger.debug("ignoring transcript while bot is speaking: %s", transcript[:40])
                return

            self._reload_mode()

            speaker_id = getattr(frame, "speaker_id", None)
            speaker_confidence = getattr(frame, "speaker_confidence", None)
            speaker_inherited = getattr(frame, "speaker_inherited", False)

            async with self._lock:
                if self.settings.mode == Mode.SILENT:
                    await self._store_only(
                        transcript, speaker_id, speaker_confidence
                    )
                    return

                if self.state == DeciderState.LISTENING:
                    await self._handle_listening(
                        transcript, speaker_id, speaker_confidence
                    )
                elif self.state == DeciderState.AWAITING_CONFIRMATION:
                    await self._handle_confirmation(
                        transcript, speaker_id, speaker_inherited
                    )
                elif self.state == DeciderState.EXECUTING:
                    await self._store_only(
                        transcript, speaker_id, speaker_confidence
                    )

        def _extract_transcript(self, frame) -> str | None:
            if not isinstance(frame, TranscriptionFrame):
                return None
            text = getattr(frame, "text", None) or getattr(frame, "transcript", None)
            if not text:
                return None
            return str(text).strip() or None

        async def _store_only(
            self,
            transcript: str,
            speaker_id: str | None = None,
            speaker_confidence: float | None = None,
        ) -> None:
            self._last_transcript = transcript
            await self.store.log_transcript(
                transcript,
                self.settings.mode.value,
                speaker_id=speaker_id,
                speaker_confidence=speaker_confidence,
            )

        async def _handle_listening(
            self,
            transcript: str,
            speaker_id: str | None = None,
            speaker_confidence: float | None = None,
        ) -> None:
            # Non-owner filter (Phase 1 closes the confirmation spoof):
            # when the feature is enabled, non-owner transcripts are stored
            # but never reach Claude — no decider call, no possibility of
            # transitioning to EXECUTING.
            if self.settings.speaker_id_enabled and speaker_id != "owner":
                logger.info(
                    "[DECIDER] non-owner utterance dropped: sid=%s", speaker_id
                )
                await self.store.log_transcript(
                    transcript,
                    self.settings.mode.value,
                    speaker_id=speaker_id,
                    speaker_confidence=speaker_confidence,
                )
                return
            if is_noise(transcript):
                logger.debug("noise filter dropped transcript: %r", transcript[:40])
                await self.store.log_transcript(
                    transcript,
                    self.settings.mode.value,
                    speaker_id=speaker_id,
                    speaker_confidence=speaker_confidence,
                )
                return
            if is_quick_nothing(transcript, self.settings.mode):
                logger.debug(
                    "quick-nothing filter dropped transcript: %r", transcript[:40]
                )
                await self.store.log_transcript(
                    transcript,
                    self.settings.mode.value,
                    speaker_id=speaker_id,
                    speaker_confidence=speaker_confidence,
                )
                return
            t0 = time.monotonic()
            transcript_id = await self.store.log_transcript(
                transcript,
                self.settings.mode.value,
                speaker_id=speaker_id,
                speaker_confidence=speaker_confidence,
            )
            prompt = await self._prompt_for_transcript(transcript)
            t_pre = time.monotonic()
            self._safe_emit(
                EventKind.DECIDER_START,
                transcript_id=transcript_id,
                payload={"transcript": transcript[:200], "mode": self.settings.mode.value},
            )
            try:
                decision = await self.claude_cli.call_decider(prompt)
            except Exception as e:
                logger.exception("decider call failed: %s", e)
                return
            t_decider = time.monotonic()

            decision_id = await self.store.log_decision(transcript_id, decision)
            d_type = decision.get("type", "nothing")
            self._safe_emit(
                EventKind.DECIDER_DONE,
                transcript_id=transcript_id,
                decision_id=decision_id,
                payload={
                    "type": d_type,
                    "confidence": decision.get("confidence"),
                },
            )
            logger.info(
                "[TIMING] decider transcript=%r prep=%.0fms decider=%.0fms type=%s",
                transcript[:40],
                (t_pre - t0) * 1000,
                (t_decider - t_pre) * 1000,
                d_type,
            )

            if d_type == "nothing":
                return
            if d_type == "speak":
                reply = decision.get("reply")
                if reply:
                    await self.push_frame(TTSSpeakFrame(reply))
                return
            if d_type == "act":
                confidence = decision.get("confidence", 0.0) or 0.0
                if confidence < self.settings.min_action_confidence:
                    logger.info("action below confidence floor, dropping")
                    self._safe_emit(
                        EventKind.DECIDER_DROPPED_LOW_CONF,
                        transcript_id=transcript_id,
                        decision_id=decision_id,
                        payload={
                            "confidence": confidence,
                            "floor": self.settings.min_action_confidence,
                        },
                    )
                    return
                self.pending_action = decision
                self.pending_decision_id = decision_id
                self.pending_speaker_id = speaker_id
                self.state = DeciderState.AWAITING_CONFIRMATION
                self.confirmation_deadline = (
                    time.monotonic() + self.settings.confirmation_timeout_seconds
                )
                self._schedule_timeout_task()
                intent = decision.get("intent", "do that")
                self._safe_emit(
                    EventKind.ACTION_ARMED,
                    transcript_id=transcript_id,
                    decision_id=decision_id,
                    payload={"intent": intent},
                )
                await self.push_frame(TTSSpeakFrame(f"Хочу {intent}, можна?"))

        def _schedule_timeout_task(self) -> None:
            self._cancel_timeout_task()
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            self._timeout_task = loop.create_task(self._timeout_watcher())

        def _cancel_timeout_task(self) -> None:
            task = self._timeout_task
            self._timeout_task = None
            if task is not None and not task.done():
                task.cancel()

        def _schedule_bot_cooldown(self) -> None:
            self._cancel_bot_cooldown()
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                self._bot_speaking = False
                return
            self._bot_cooldown_task = loop.create_task(self._bot_cooldown_watcher())

        def _cancel_bot_cooldown(self) -> None:
            task = self._bot_cooldown_task
            self._bot_cooldown_task = None
            if task is not None and not task.done():
                task.cancel()

        async def _bot_cooldown_watcher(self) -> None:
            try:
                await asyncio.sleep(self.settings.bot_speaking_cooldown_seconds)
            except asyncio.CancelledError:
                return
            self._bot_speaking = False

        async def _timeout_watcher(self) -> None:
            try:
                await asyncio.sleep(self.settings.confirmation_timeout_seconds)
            except asyncio.CancelledError:
                return
            async with self._lock:
                if (
                    self.state == DeciderState.AWAITING_CONFIRMATION
                    and self.confirmation_deadline is not None
                    and time.monotonic() >= self.confirmation_deadline
                ):
                    await self._cancel_pending("nevermind, cancelled")

        async def _handle_confirmation(
            self,
            transcript: str,
            speaker_id: str | None = None,
            speaker_inherited: bool = False,
        ) -> None:
            await self.store.log_transcript(
                transcript,
                self.settings.mode.value,
                speaker_id=speaker_id,
            )
            # Short-turn fail-closed: inherited labels are NEVER trusted in
            # AWAITING_CONFIRMATION — a 350 ms "так" from a stranger right
            # after an owner utterance would inherit owner's id and pass
            # otherwise. See plan §4 short-turn fail-closed.
            if self.settings.speaker_id_enabled and speaker_inherited:
                logger.warning(
                    "ignoring inherited-label confirmation, pending was %s",
                    self.pending_speaker_id,
                )
                await self.push_frame(TTSSpeakFrame("Скажи: так чи ні?"))
                return
            if self.settings.speaker_id_enabled and speaker_id != self.pending_speaker_id:
                logger.warning(
                    "speaker mismatch on confirmation: got %s, expected %s",
                    speaker_id,
                    self.pending_speaker_id,
                )
                await self.push_frame(TTSSpeakFrame("Скажи: так чи ні?"))
                return
            verdict = parse_yes_no(transcript)
            if verdict == "unclear":
                self._safe_emit(
                    EventKind.ACTION_REPROMPT,
                    decision_id=self.pending_decision_id,
                )
                await self.push_frame(TTSSpeakFrame("Скажи: так чи ні?"))
                return
            if verdict == "no":
                self._safe_emit(
                    EventKind.ACTION_CANCELLED,
                    decision_id=self.pending_decision_id,
                    payload={"reason": "user said no"},
                )
                await self._cancel_pending("okay")
                return
            if verdict == "yes":
                self._safe_emit(
                    EventKind.ACTION_CONFIRMED,
                    decision_id=self.pending_decision_id,
                )
                await self._execute_pending()

        async def _execute_pending(self) -> None:
            if self.pending_action is None:
                self.state = DeciderState.LISTENING
                return
            self.state = DeciderState.EXECUTING
            action = self.pending_action
            decision_id = self.pending_decision_id
            description = action.get("intent") or str(action.get("action"))
            self._safe_emit(
                EventKind.ACTION_EXECUTING,
                decision_id=decision_id,
                payload={"intent": description[:200]},
            )

            def _stdout_emit(line: str) -> None:
                # Per-line stdout from the `claude -p` subprocess surfaces
                # in the progress dashboard during long actions. 8 KB cap
                # protects the queue from a single runaway line.
                self._safe_emit(
                    EventKind.ACTION_STDOUT,
                    decision_id=decision_id,
                    payload={"line": line[:8192]},
                )

            try:
                self._safe_emit(
                    EventKind.ACTION_CALL_START,
                    decision_id=decision_id,
                )
                result = await self.claude_cli.call_action(
                    description, on_line=_stdout_emit
                )
                summary = result.get("summary", "done")
                if decision_id is not None:
                    await self.store.log_action(decision_id, "ok", summary)
                self._safe_emit(
                    EventKind.ACTION_DONE,
                    decision_id=decision_id,
                    payload={"summary": (summary or "")[:200]},
                )
                await self.push_frame(TTSSpeakFrame(summary))
            except Exception as e:
                logger.exception("action failed: %s", e)
                if decision_id is not None:
                    await self.store.log_action(decision_id, "error", str(e))
                self._safe_emit(
                    EventKind.ACTION_ERROR,
                    decision_id=decision_id,
                    payload={"error": str(e)[:200]},
                )
                await self.push_frame(TTSSpeakFrame("дія не вдалася"))
            finally:
                self.pending_action = None
                self.pending_decision_id = None
                self.pending_speaker_id = None
                self.confirmation_deadline = None
                self.state = DeciderState.LISTENING
                self._cancel_timeout_task()
                self._safe_emit(EventKind.STATE_LISTENING)

        async def _cancel_pending(self, message: str) -> None:
            if self.pending_decision_id is not None:
                await self.store.log_action(
                    self.pending_decision_id, "cancelled", message
                )
            self.pending_action = None
            self.pending_decision_id = None
            self.pending_speaker_id = None
            self.confirmation_deadline = None
            self.state = DeciderState.LISTENING
            self._cancel_timeout_task()
            self._safe_emit(EventKind.STATE_LISTENING)
            await self.push_frame(TTSSpeakFrame(message))

        async def on_heartbeat_tick(self) -> None:
            if self.state != DeciderState.LISTENING:
                return
            if self.settings.mode == Mode.SILENT:
                return
            ctx = await self.context_builder.build(transcript=None, heartbeat=True)
            prompt = self.context_builder.render(self.decider_prompt_template, ctx)
            try:
                decision = await self.claude_cli.call_decider(prompt)
            except Exception as e:
                logger.exception("heartbeat decider call failed: %s", e)
                return
            decided_to_speak = decision.get("type") == "speak"
            reply = decision.get("reply")
            await self.store.log_heartbeat(decided_to_speak, reply)
            if decided_to_speak and reply:
                await self.push_frame(TTSSpeakFrame(reply))

    _decider_cls = DeciderProcessor
    return DeciderProcessor


def create_decider_processor(
    claude_cli: "ClaudeCLI",
    store: "TranscriptStore",
    context_builder: "ContextBuilder",
    settings: Settings,
    decider_prompt_template: str,
):
    cls = _build_decider_processor_class()
    return cls(claude_cli, store, context_builder, settings, decider_prompt_template)
