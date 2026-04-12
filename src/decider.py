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

if TYPE_CHECKING:
    from .claude_cli import ClaudeCLI
    from .context import ContextBuilder
    from .storage import TranscriptStore


logger = logging.getLogger("heare.decider")


def _load_pipecat_base():
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
    from pipecat.frames.frames import Frame, TextFrame, TranscriptionFrame

    return FrameProcessor, FrameDirection, Frame, TextFrame, TranscriptionFrame


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
        TextFrame,
        TranscriptionFrame,
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
            self.confirmation_deadline: float | None = None
            self._last_transcript: str | None = None
            self._lock = asyncio.Lock()
            self._timeout_task: asyncio.Task | None = None

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

            transcript = self._extract_transcript(frame)
            if transcript is None:
                await self.push_frame(frame, direction)
                return

            self._reload_mode()

            async with self._lock:
                if self.settings.mode == Mode.SILENT:
                    await self._store_only(transcript)
                    return

                if self.state == DeciderState.LISTENING:
                    await self._handle_listening(transcript)
                elif self.state == DeciderState.AWAITING_CONFIRMATION:
                    await self._handle_confirmation(transcript)
                elif self.state == DeciderState.EXECUTING:
                    await self._store_only(transcript)

        def _extract_transcript(self, frame) -> str | None:
            if not isinstance(frame, TranscriptionFrame):
                return None
            text = getattr(frame, "text", None) or getattr(frame, "transcript", None)
            if not text:
                return None
            return str(text).strip() or None

        async def _store_only(self, transcript: str) -> None:
            self._last_transcript = transcript
            await self.store.log_transcript(transcript, self.settings.mode.value)

        async def _handle_listening(self, transcript: str) -> None:
            transcript_id = await self.store.log_transcript(
                transcript, self.settings.mode.value
            )
            ctx = await self.context_builder.build(transcript, heartbeat=False)
            prompt = self.context_builder.render(self.decider_prompt_template, ctx)
            try:
                decision = await self.claude_cli.call_decider(prompt)
            except Exception as e:
                logger.exception("decider call failed: %s", e)
                return

            decision_id = await self.store.log_decision(transcript_id, decision)
            d_type = decision.get("type", "nothing")

            if d_type == "nothing":
                return
            if d_type == "speak":
                reply = decision.get("reply")
                if reply:
                    await self.push_frame(TextFrame(reply))
                return
            if d_type == "act":
                confidence = decision.get("confidence", 0.0) or 0.0
                if confidence < 0.8:
                    logger.info("action below confidence floor, dropping")
                    return
                self.pending_action = decision
                self.pending_decision_id = decision_id
                self.state = DeciderState.AWAITING_CONFIRMATION
                self.confirmation_deadline = (
                    time.monotonic() + self.settings.confirmation_timeout_seconds
                )
                self._schedule_timeout_task()
                intent = decision.get("intent", "do that")
                await self.push_frame(TextFrame(f"Хочу {intent}, можна?"))

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

        async def _handle_confirmation(self, transcript: str) -> None:
            await self.store.log_transcript(transcript, self.settings.mode.value)
            verdict = parse_yes_no(transcript)
            if verdict == "unclear":
                await self.push_frame(TextFrame("Скажи: так чи ні?"))
                return
            if verdict == "no":
                await self._cancel_pending("okay")
                return
            if verdict == "yes":
                await self._execute_pending()

        async def _execute_pending(self) -> None:
            if self.pending_action is None:
                self.state = DeciderState.LISTENING
                return
            self.state = DeciderState.EXECUTING
            action = self.pending_action
            decision_id = self.pending_decision_id
            description = action.get("intent") or str(action.get("action"))
            try:
                result = await self.claude_cli.call_action(description)
                summary = result.get("summary", "done")
                if decision_id is not None:
                    await self.store.log_action(decision_id, "ok", summary)
                await self.push_frame(TextFrame(summary))
            except Exception as e:
                logger.exception("action failed: %s", e)
                if decision_id is not None:
                    await self.store.log_action(decision_id, "error", str(e))
                await self.push_frame(TextFrame("дія не вдалася"))
            finally:
                self.pending_action = None
                self.pending_decision_id = None
                self.confirmation_deadline = None
                self.state = DeciderState.LISTENING
                self._cancel_timeout_task()

        async def _cancel_pending(self, message: str) -> None:
            if self.pending_decision_id is not None:
                await self.store.log_action(
                    self.pending_decision_id, "cancelled", message
                )
            self.pending_action = None
            self.pending_decision_id = None
            self.confirmation_deadline = None
            self.state = DeciderState.LISTENING
            self._cancel_timeout_task()
            await self.push_frame(TextFrame(message))

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
                await self.push_frame(TextFrame(reply))

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
