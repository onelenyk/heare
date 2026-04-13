"""AudioBufferProcessor + SpeakerTaggerProcessor — race-safe speaker identity.

Pipecat imports are deferred inside _build_processor_classes so admin CLI
paths and tests work without portaudio/pipecat installed.

Key invariants:
- Per-turn `_TurnSlot` dict keyed by monotonic turn_id.
- `AudioBufferProcessor.process_frame(UserStoppedSpeakingFrame)` kicks off
  a fire-and-forget `asyncio.Task` that runs speaker_id.embed() in an
  executor. This is the real parallelism seam with Groq STT — while STT
  uploads audio, ECAPA runs on the CPU.
- `SpeakerTaggerProcessor.process_frame(TranscriptionFrame)` awaits the
  matching slot's `asyncio.Event` with a 200 ms bounded timeout and
  fails closed to `speaker_id=None` on timeout.
- Tagger skips ECAPA entirely while bot is speaking (own `_bot_speaking`
  flag + Bot*SpeakingFrame subscription — mirrors decider.py:347-355).
- Short turns (<400ms) in LISTENING inherit prev label; decider gates
  inherited confirmations as fail-closed (done in decider.py).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from . import speaker_id
from .config import Settings

if TYPE_CHECKING:
    from .speaker_gallery import SpeakerGallery


logger = logging.getLogger("heare.speaker_processor")


@dataclass
class _TurnSlot:
    turn_id: int
    pcm: bytes | None = None
    embedding: np.ndarray | None = None
    error: Exception | None = None
    elapsed_ms: float = 0.0
    duration_ms: float = 0.0
    event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None


_SLOT_RETAIN = 4


def _load_pipecat_base() -> tuple[Any, ...]:
    from pipecat.frames.frames import (  # type: ignore
        AudioRawFrame,
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        Frame,
        InterimTranscriptionFrame,
        TranscriptionFrame,
        UserStartedSpeakingFrame,
        UserStoppedSpeakingFrame,
    )
    from pipecat.processors.frame_processor import (  # type: ignore
        FrameDirection,
        FrameProcessor,
    )

    return (
        FrameProcessor,
        FrameDirection,
        Frame,
        AudioRawFrame,
        TranscriptionFrame,
        InterimTranscriptionFrame,
        UserStartedSpeakingFrame,
        UserStoppedSpeakingFrame,
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
    )


_buffer_cls: Any = None
_tagger_cls: Any = None


def _build_processor_classes() -> tuple[Any, Any]:
    global _buffer_cls, _tagger_cls
    if _buffer_cls is not None and _tagger_cls is not None:
        return _buffer_cls, _tagger_cls
    (
        FrameProcessor,
        FrameDirection,
        Frame,
        AudioRawFrame,
        TranscriptionFrame,
        InterimTranscriptionFrame,
        UserStartedSpeakingFrame,
        UserStoppedSpeakingFrame,
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
    ) = _load_pipecat_base()

    class AudioBufferProcessor(FrameProcessor):  # type: ignore[misc,valid-type]
        """Captures raw PCM between UserStartedSpeakingFrame and
        UserStoppedSpeakingFrame; kicks off fire-and-forget ECAPA embed on
        each stop so the result is ready by the time TranscriptionFrame
        arrives at the downstream tagger.
        """

        def __init__(
            self,
            settings: Settings,
            model: Any,
            sample_rate: int = 16000,
        ) -> None:
            super().__init__()
            self._settings = settings
            self._model = model
            self._sample_rate = sample_rate
            self._slots: dict[int, _TurnSlot] = {}
            self._next_turn_id = 1
            self._current_turn: _TurnSlot | None = None
            self._chunks: list[bytes] = []
            self._latest_completed_turn_id: int | None = None

        def get_slot(self, turn_id: int) -> _TurnSlot | None:
            return self._slots.get(turn_id)

        def latest_completed_turn_id(self) -> int | None:
            return self._latest_completed_turn_id

        def _gc_old_slots(self) -> None:
            if len(self._slots) <= _SLOT_RETAIN:
                return
            # Keep the N most recent by turn_id; cancel any in-flight task
            # on evicted slots so torch tensors don't leak.
            keep_ids = sorted(self._slots.keys())[-_SLOT_RETAIN:]
            to_evict = [tid for tid in self._slots if tid not in keep_ids]
            for tid in to_evict:
                slot = self._slots.pop(tid)
                if slot.task is not None and not slot.task.done():
                    slot.task.cancel()

        async def close(self) -> None:
            pending: list[asyncio.Task] = []
            for slot in list(self._slots.values()):
                if slot.task is not None and not slot.task.done():
                    slot.task.cancel()
                    pending.append(slot.task)
            if not pending:
                return
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True), timeout=1.0
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "speaker buffer close: %d tasks did not cancel within 1s",
                    len(pending),
                )

        async def process_frame(self, frame, direction) -> None:  # type: ignore[override]
            await super().process_frame(frame, direction)

            if isinstance(frame, UserStartedSpeakingFrame):
                slot = _TurnSlot(turn_id=self._next_turn_id)
                self._slots[slot.turn_id] = slot
                self._current_turn = slot
                self._next_turn_id += 1
                self._chunks = []
                self._gc_old_slots()
                await self.push_frame(frame, direction)
                return

            if isinstance(frame, AudioRawFrame):
                if frame.sample_rate != self._sample_rate:
                    raise RuntimeError(
                        f"speaker buffer requires {self._sample_rate} Hz PCM, "
                        f"got {frame.sample_rate}. Set audio_in_sample_rate=16000 "
                        f"on LocalAudioTransportParams."
                    )
                if self._current_turn is not None:
                    self._chunks.append(frame.audio)
                await self.push_frame(frame, direction)
                return

            if isinstance(frame, UserStoppedSpeakingFrame):
                slot = self._current_turn
                self._current_turn = None
                if slot is not None:
                    slot.pcm = b"".join(self._chunks)
                    self._chunks = []
                    # duration_ms from int16 mono PCM length
                    slot.duration_ms = (len(slot.pcm) / 2) / self._sample_rate * 1000.0
                    try:
                        loop = asyncio.get_running_loop()
                        slot.task = loop.create_task(self._run_embed(slot))
                    except RuntimeError:
                        slot.error = RuntimeError("no running event loop")
                        slot.event.set()
                await self.push_frame(frame, direction)
                return

            await self.push_frame(frame, direction)

        async def _run_embed(self, slot: _TurnSlot) -> None:
            t0 = time.monotonic()
            try:
                loop = asyncio.get_running_loop()
                slot.embedding = await loop.run_in_executor(
                    None, speaker_id.embed, slot.pcm, self._sample_rate, self._model
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                slot.error = e
                logger.warning("speaker embed failed for turn %d: %s", slot.turn_id, e)
            finally:
                slot.elapsed_ms = (time.monotonic() - t0) * 1000.0
                self._latest_completed_turn_id = slot.turn_id
                slot.event.set()

    class SpeakerTaggerProcessor(FrameProcessor):  # type: ignore[misc,valid-type]
        """Reads the latest _TurnSlot from AudioBufferProcessor, awaits its
        embed with a bounded timeout, identifies the speaker via the gallery,
        and mutates the TranscriptionFrame with speaker_id / speaker_confidence
        / speaker_label / speaker_inherited / speaker_turn_id attributes.
        """

        def __init__(
            self,
            buffer: AudioBufferProcessor,
            gallery: "SpeakerGallery",
            settings: Settings,
        ) -> None:
            super().__init__()
            self._buffer = buffer
            self._gallery = gallery
            self._settings = settings
            self._bot_speaking = False
            self._bot_cooldown_task: asyncio.Task | None = None
            self._prev_id: str | None = None

        def _schedule_bot_cooldown(self) -> None:
            if self._bot_cooldown_task is not None and not self._bot_cooldown_task.done():
                self._bot_cooldown_task.cancel()
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                self._bot_speaking = False
                return
            self._bot_cooldown_task = loop.create_task(self._bot_cooldown_watcher())

        async def _bot_cooldown_watcher(self) -> None:
            try:
                await asyncio.sleep(self._settings.bot_speaking_cooldown_seconds)
            except asyncio.CancelledError:
                return
            self._bot_speaking = False

        async def process_frame(self, frame, direction) -> None:  # type: ignore[override]
            await super().process_frame(frame, direction)

            if isinstance(frame, BotStartedSpeakingFrame):
                self._bot_speaking = True
                if self._bot_cooldown_task is not None and not self._bot_cooldown_task.done():
                    self._bot_cooldown_task.cancel()
                await self.push_frame(frame, direction)
                return
            if isinstance(frame, BotStoppedSpeakingFrame):
                self._schedule_bot_cooldown()
                await self.push_frame(frame, direction)
                return

            # Ignore interim (partial) transcriptions — only act on finalized
            # ones. Both InterimTranscriptionFrame (separate class in pipecat
            # ≥0.0.100) AND TranscriptionFrame(finalized=False) from Groq STT
            # are treated the same: pushed through without speaker tagging.
            if isinstance(frame, InterimTranscriptionFrame):
                await self.push_frame(frame, direction)
                return

            if isinstance(frame, TranscriptionFrame):
                if not getattr(frame, "finalized", True):
                    await self.push_frame(frame, direction)
                    return
                await self._tag_transcription(frame)
                await self.push_frame(frame, direction)
                return

            await self.push_frame(frame, direction)

        async def _tag_transcription(self, frame) -> None:
            # Default attrs so downstream always sees them
            frame.speaker_id = None
            frame.speaker_label = None
            frame.speaker_confidence = 0.0
            frame.speaker_inherited = False
            frame.speaker_turn_id = None

            if self._bot_speaking:
                frame.speaker_confidence = -1.0
                logger.info(
                    "[SPEAKER] turn=? sid=None conf=-1.00 inherited=False (bot speaking)"
                )
                return

            turn_id = self._buffer.latest_completed_turn_id()
            if turn_id is None:
                logger.debug("no completed turn slot yet, fail-closed")
                return
            slot = self._buffer.get_slot(turn_id)
            if slot is None:
                return

            try:
                await asyncio.wait_for(slot.event.wait(), timeout=0.2)
            except asyncio.TimeoutError:
                logger.warning(
                    "[SPEAKER] turn=%d slot timeout — fail closed", turn_id
                )
                return

            frame.speaker_turn_id = turn_id

            if slot.error is not None or slot.embedding is None:
                logger.warning(
                    "[SPEAKER] turn=%d embed error: %s — fail closed",
                    turn_id,
                    slot.error,
                )
                return

            # Short-turn fast-reject: inherit prev label (LISTENING path uses
            # this as "same speaker continuation"; AWAITING_CONFIRMATION in
            # decider.py rejects inherited confirmations as fail-closed).
            if slot.duration_ms < self._settings.speaker_id_min_duration_ms:
                frame.speaker_id = self._prev_id
                frame.speaker_confidence = -1.0
                frame.speaker_inherited = True
                logger.info(
                    "[SPEAKER] turn=%d sid=%s conf=-1.00 inherited=True elapsed_ms=%.0f",
                    turn_id,
                    self._prev_id,
                    slot.elapsed_ms,
                )
                return

            sid, score = self._gallery.identify(
                slot.embedding,
                threshold_match=self._settings.speaker_id_threshold_match,
            )
            frame.speaker_id = sid
            frame.speaker_confidence = float(score)
            if sid is not None:
                frame.speaker_label = self._gallery.get_label(sid)
                self._prev_id = sid
            # NOTE: never log speaker_label — labels are user-controlled PII.
            logger.info(
                "[SPEAKER] turn=%d sid=%s conf=%.2f dur_ms=%.0f embed_ms=%.0f",
                turn_id,
                sid,
                score,
                slot.duration_ms,
                slot.elapsed_ms,
            )

    _buffer_cls = AudioBufferProcessor
    _tagger_cls = SpeakerTaggerProcessor
    return _buffer_cls, _tagger_cls


def create_speaker_processors(
    settings: Settings,
    gallery: "SpeakerGallery",
    model: Any,
    sample_rate: int = 16000,
) -> tuple[Any, Any]:
    buffer_cls, tagger_cls = _build_processor_classes()
    buffer = buffer_cls(settings, model, sample_rate)
    tagger = tagger_cls(buffer, gallery, settings)
    return buffer, tagger
