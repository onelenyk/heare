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
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from . import speaker_id
from .config import Settings

if TYPE_CHECKING:
    from .speaker_gallery import SpeakerGallery

# Signature for the tagger's optional namer hook. Kept as an Any alias so
# speaker_processor stays free of the speaker_namer import at module load
# time — the concrete TurnRecord is imported lazily inside _tag_transcription.
NamerEnqueue = Any  # Callable[[TurnRecord], None]


logger = logging.getLogger("heare.speaker_processor")


@dataclass
class _TurnSlot:
    turn_id: int
    pcm: bytes | None = None
    embedding: np.ndarray | None = None
    accum_embedding: np.ndarray | None = None
    error: Exception | None = None
    elapsed_ms: float = 0.0
    duration_ms: float = 0.0
    event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None


_SLOT_RETAIN = 4


class _AccumBuffer:
    """Rolling PCM buffer bounded by duration.

    Kept at up to 2x the target duration as headroom so a short turn still
    has a recent window of audio that reaches ECAPA's stable regime (~3s).
    Flushed on BotStartedSpeakingFrame so TTS audio never contaminates the
    accumulator.
    """

    def __init__(self, target_ms: int, sample_rate: int) -> None:
        self.target_ms = target_ms
        self.sample_rate = sample_rate
        self._chunks: deque[bytes] = deque()
        self._total_bytes = 0

    def append(self, pcm: bytes) -> None:
        self._chunks.append(pcm)
        self._total_bytes += len(pcm)
        cap_bytes = int((2 * self.target_ms / 1000.0) * self.sample_rate * 2)
        while self._total_bytes > cap_bytes and len(self._chunks) > 1:
            oldest = self._chunks.popleft()
            self._total_bytes -= len(oldest)

    def pcm_bytes(self) -> bytes:
        return b"".join(self._chunks)

    def total_ms(self) -> float:
        return (self._total_bytes / 2) / self.sample_rate * 1000.0

    def flush(self) -> None:
        self._chunks.clear()
        self._total_bytes = 0


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

    from .indication import IndicationCueFrame

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
        IndicationCueFrame,
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
        IndicationCueFrame,
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
            self._accum = _AccumBuffer(
                target_ms=settings.speaker_id_accum_target_ms,
                sample_rate=sample_rate,
            )

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
            self._accum.flush()
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

            if isinstance(frame, BotStartedSpeakingFrame):
                # TTS audio must never contaminate the rolling accumulator.
                self._accum.flush()
                await self.push_frame(frame, direction)
                return

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
                    # SECURITY: the accumulator is a cross-turn rolling
                    # window. Widening speaker_id_accum_target_ms raises the
                    # risk of a stranger's short turn being misclassified
                    # because owner audio still in the buffer can elevate a
                    # mixed embedding above threshold. Re-audit the decider
                    # confirmation gate if you change that setting.
                    self._accum.append(frame.audio)
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
                # ECAPA is trained on ~3s clips; fire a second embed on the
                # rolling window only when this turn would score below the
                # stable regime on its own.
                target_ms = self._settings.speaker_id_accum_target_ms
                if (
                    slot.duration_ms < target_ms
                    and self._accum.total_ms() >= target_ms
                ):
                    try:
                        accum_pcm = self._accum.pcm_bytes()
                        slot.accum_embedding = await loop.run_in_executor(
                            None,
                            speaker_id.embed,
                            accum_pcm,
                            self._sample_rate,
                            self._model,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            "speaker accum embed failed for turn %d: %s",
                            slot.turn_id,
                            e,
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
            namer_enqueue: NamerEnqueue | None = None,
        ) -> None:
            super().__init__()
            self._buffer = buffer
            self._gallery = gallery
            self._settings = settings
            self._namer_enqueue = namer_enqueue
            self._bot_speaking = False
            self._bot_cooldown_task: asyncio.Task | None = None
            # Indication-cue echo gate — set while a non-speech cue is playing.
            self._indication_speaking = False
            self._prev_id: str | None = None
            self._prev_at: float = 0.0
            # Session-local rolling buffer of recent non-owner embeddings.
            # Never persisted, cleared on successful auto-enroll and on
            # bot_speaking so TTS echo cannot poison the stranger cluster.
            self._stranger_candidates: deque[np.ndarray] = deque(maxlen=5)

        def _maybe_auto_enroll(self, new_embed: np.ndarray) -> None:
            threshold = self._settings.speaker_id_threshold_match
            matches = 0
            new_norm = float(np.linalg.norm(new_embed)) + 1e-12
            for prior in self._stranger_candidates:
                prior_norm = float(np.linalg.norm(prior)) + 1e-12
                cos = float(np.dot(prior, new_embed) / (prior_norm * new_norm))
                if cos >= threshold:
                    matches += 1
            self._stranger_candidates.append(new_embed)

            # Owner auto-enroll runs first and only when no owner exists.
            # The threshold is intentionally higher than guest's (default 5
            # vs 2) because mis-enrolling the owner is harder to undo than
            # mis-enrolling a guest. Existing owner is never overwritten.
            #
            # While the owner slot is open and owner-enroll is enabled, the
            # guest path is suppressed entirely — otherwise the lower guest
            # threshold (default 2) would always fire first and the owner
            # slot would never be filled by auto-enrollment.
            owner_enabled = self._settings.speaker_id_auto_enroll_owner_enabled
            owner_needed = self._settings.speaker_id_auto_enroll_owner_after
            owner_slot_open = (
                owner_enabled and "owner" not in self._gallery.list_speakers()
            )
            if owner_slot_open and (matches + 1) >= owner_needed:
                try:
                    self._gallery.enroll_owner(new_embed, "owner")
                except Exception as e:  # noqa: BLE001
                    logger.warning("owner auto-enroll failed: %s", e)
                    return
                logger.info(
                    "[SPEAKER] auto-enrolled owner after %d matching turns",
                    matches + 1,
                )
                self._stranger_candidates.clear()
                try:
                    from .indication import IndicationKind, get_indication

                    ind = get_indication()
                    if ind is not None:
                        ind.notify(
                            IndicationKind.OWNER_AUTO_ENROLLED,
                            body=f"learned after {matches + 1} turns",
                        )
                except Exception:  # noqa: BLE001
                    logger.warning("owner enroll indication notify failed", exc_info=True)
                try:
                    from pipecat.frames.frames import TTSSpeakFrame  # type: ignore

                    loop = asyncio.get_running_loop()
                    loop.create_task(
                        self.push_frame(TTSSpeakFrame("Тепер я впізнаю твій голос."))
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("owner enroll TTS push failed: %s", e)
                return
            if owner_slot_open:
                # Owner threshold not yet met — let candidates accumulate
                # without prematurely consuming them as a guest enrollment.
                return

            needed = self._settings.speaker_id_auto_enroll_after
            if (matches + 1) >= needed:
                try:
                    guest_id = self._gallery.enroll_guest(new_embed)
                except Exception as e:  # noqa: BLE001
                    logger.warning("auto-enroll failed: %s", e)
                    return
                if guest_id:
                    logger.info(
                        "[SPEAKER] auto-enrolled %s after %d matching turns",
                        guest_id,
                        matches + 1,
                    )
                    self._stranger_candidates.clear()
                    try:
                        from .indication import IndicationKind, get_indication

                        ind = get_indication()
                        if ind is not None:
                            ind.notify(
                                IndicationKind.GUEST_AUTO_ENROLLED,
                                body=guest_id,
                            )
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "guest enroll indication notify failed", exc_info=True
                        )

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

            if isinstance(frame, IndicationCueFrame):
                self._indication_speaking = bool(frame.start)
                await self.push_frame(frame, direction)
                return

            if isinstance(frame, BotStartedSpeakingFrame):
                self._bot_speaking = True
                if self._bot_cooldown_task is not None and not self._bot_cooldown_task.done():
                    self._bot_cooldown_task.cancel()
                # A stranger's candidate buffer must not bleed across a TTS
                # turn — own voice echoing back could poison the cluster.
                self._stranger_candidates.clear()
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

            from .indication import is_enrollment_active
            enrollment_active = is_enrollment_active()
            if self._bot_speaking or self._indication_speaking or enrollment_active:
                frame.speaker_confidence = -1.0
                state = []
                if self._bot_speaking:
                    state.append("bot")
                if self._indication_speaking:
                    state.append("indication")
                if enrollment_active:
                    state.append("enrollment")
                logger.info(
                    "[SPEAKER] turn=? sid=None conf=-1.00 inherited=False (%s speaking/recording)",
                    "/".join(state),
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

            # Short-turn fast-reject: inherit prev label only when we still
            # hold a trusted prev identity AND the sticky window has not
            # expired. Outside the window (or without prev), a short turn
            # falls through as unknown — the decider's fail-closed gate in
            # AWAITING_CONFIRMATION handles the rest.
            if slot.duration_ms < self._settings.speaker_id_min_duration_ms:
                sticky_ok = (
                    self._prev_id is not None
                    and (time.monotonic() - self._prev_at)
                    < self._settings.speaker_id_sticky_seconds
                )
                if sticky_ok:
                    frame.speaker_id = self._prev_id
                    frame.speaker_confidence = -1.0
                    frame.speaker_inherited = True
                    logger.info(
                        "[SPEAKER] turn=%d sid=%s conf=-1.00 inherited=True elapsed_ms=%.0f",
                        turn_id,
                        self._prev_id,
                        slot.elapsed_ms,
                    )
                else:
                    logger.info(
                        "[SPEAKER] turn=%d sid=None inherited=False (sticky expired or no prev) elapsed_ms=%.0f",
                        turn_id,
                        slot.elapsed_ms,
                    )
                return

            # Marginal-duration turns (between min_duration_ms and the
            # accum target) prefer the rolling-window embedding — see
            # _run_embed for the stable-regime rationale.
            target_ms = self._settings.speaker_id_accum_target_ms
            using_accum = (
                slot.duration_ms < target_ms and slot.accum_embedding is not None
            )
            embed_vec = slot.accum_embedding if using_accum else slot.embedding
            sid, score = self._gallery.identify(
                embed_vec,
                threshold_match=self._settings.speaker_id_threshold_match,
            )
            frame.speaker_id = sid
            frame.speaker_confidence = float(score)
            if sid is not None:
                frame.speaker_label = self._gallery.get_label(sid)
                self._prev_id = sid
                self._prev_at = time.monotonic()
                # Session ref: register EVERY successful match as an
                # in-memory anchor. Rapid adaptation to the current room.
                # Never persisted — forgotten on restart.
                if slot.embedding is not None:
                    self._gallery.register_session_ref(sid, slot.embedding)
                # Persistent auto-append: only on HIGH-confidence single-
                # turn embeddings. Long-term memory stays clean.
                if (
                    slot.embedding is not None
                    and score >= self._settings.speaker_id_threshold_match + 0.05
                ):
                    try:
                        self._gallery.append_reference(sid, slot.embedding)
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            "auto-append failed for turn %d: %s", turn_id, e
                        )
            else:
                # Non-match on a non-short turn invalidates any prior sticky
                # label — a stranger (or low-confidence owner) must not leak
                # "owner" forward to the next short turn.
                self._prev_id = None
                self._prev_at = 0.0
                if (
                    self._settings.speaker_id_auto_enroll_enabled
                    and slot.embedding is not None
                    and slot.duration_ms >= self._settings.speaker_id_min_duration_ms
                ):
                    self._maybe_auto_enroll(slot.embedding)
            # NOTE: never log speaker_label — labels are user-controlled PII.
            logger.info(
                "[SPEAKER] turn=%d sid=%s conf=%.2f dur_ms=%.0f embed_ms=%.0f using_accum=%s",
                turn_id,
                sid,
                score,
                slot.duration_ms,
                slot.elapsed_ms,
                using_accum,
            )
            # Parallel "who talks" pipeline: hand tagged guest turns to the
            # namer. Guarded so a namer queue stall/exception never blocks
            # the audio path. Owner is excluded — it's already named.
            if (
                self._namer_enqueue is not None
                and sid is not None
                and sid != "owner"
            ):
                text = getattr(frame, "text", "") or ""
                if text.strip():
                    try:
                        from .speaker_namer import TurnRecord  # local import keeps module lazy

                        self._namer_enqueue(
                            TurnRecord(
                                speaker_id=sid,
                                text=text,
                                timestamp=time.time(),
                                turn_id=turn_id,
                            )
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "namer enqueue raised for turn=%d sid=%s — dropping",
                            turn_id,
                            sid,
                            exc_info=True,
                        )

    _buffer_cls = AudioBufferProcessor
    _tagger_cls = SpeakerTaggerProcessor
    return _buffer_cls, _tagger_cls


def create_speaker_processors(
    settings: Settings,
    gallery: "SpeakerGallery",
    model: Any,
    sample_rate: int = 16000,
    namer_enqueue: NamerEnqueue | None = None,
) -> tuple[Any, Any]:
    buffer_cls, tagger_cls = _build_processor_classes()
    buffer = buffer_cls(settings, model, sample_rate)
    tagger = tagger_cls(buffer, gallery, settings, namer_enqueue=namer_enqueue)
    return buffer, tagger
