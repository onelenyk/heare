"""Indication subsystem facade.

A single sync, thread-safe `Indication.notify()` used by every producer in the
daemon to surface state to the user via three independent backends (sound,
visual, macOS Notification Center). Producers stay backend-agnostic; the
facade handles mode gating, quiet hours, cooldown coalescing, and dispatch.

See .omc/plans/indication.md for the full design.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Protocol

from pipecat.frames.frames import SystemFrame  # type: ignore

from src.config import IndicationSettings, Mode

logger = logging.getLogger("heare.indication")


@dataclass
class IndicationCueFrame(SystemFrame):
    """Brackets a non-speech cue's playback window.

    MUST inherit from `pipecat.frames.frames.SystemFrame` so pipecat broadcasts
    both upstream and downstream — required for upstream gating in
    decider/generator/speaker_tagger to fire correctly. Future maintainers:
    do not downgrade to a regular Frame.
    """

    start: bool = True


class IndicationKind(str, Enum):
    # Tier 1
    ACTION_FAILED = "action_failed"
    ACTION_LONG_RUNNING = "action_long_running"
    ACTION_REJECTED = "action_rejected"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMATION_DEADLINE = "confirmation_deadline"
    STT_ERROR = "stt_error"
    TTS_FAILURE = "tts_failure"
    AUDIO_DEVICE_LOST = "audio_device_lost"
    MCP_AUTH_REQUIRED = "mcp_auth_required"
    # Tier 2
    INTENT_SUBMITTED = "intent_submitted"
    INTENT_COMPLETED = "intent_completed"
    INTENT_CANCELLED = "intent_cancelled"
    WAKE_WORD_DETECTED = "wake_word_detected"
    MODE_CHANGED = "mode_changed"
    DAEMON_STARTED = "daemon_started"
    DAEMON_SHUTDOWN = "daemon_shutdown"
    CONFIRMATION_TIMED_OUT = "confirmation_timed_out"
    MCP_DISCONNECTED = "mcp_disconnected"
    WORKFLOW_SAVED = "workflow_saved"
    WORKFLOW_RUN_START = "workflow_run_start"
    WORKFLOW_RUN_END = "workflow_run_end"
    # Tier 3
    MULTI_INTENT_START = "multi_intent_start"
    MULTI_INTENT_END = "multi_intent_end"
    NETWORK_UNREACHABLE = "network_unreachable"
    HEARTBEAT_TICK = "heartbeat_tick"
    NEW_CONVERSATION = "new_conversation"


class IndicationLevel(str, Enum):
    ATTENTION = "attention"
    ERROR = "error"
    LONG_RUNNING = "long_running"
    SUCCESS = "success"
    INFO = "info"
    INPUT_WAITING = "input_waiting"
    COUNTDOWN = "countdown"


KIND_TO_LEVEL: dict[IndicationKind, IndicationLevel] = {
    # Tier 1 attention/error/input_waiting
    IndicationKind.AWAITING_CONFIRMATION: IndicationLevel.INPUT_WAITING,
    IndicationKind.CONFIRMATION_DEADLINE: IndicationLevel.ATTENTION,
    IndicationKind.MCP_AUTH_REQUIRED: IndicationLevel.INPUT_WAITING,
    IndicationKind.ACTION_FAILED: IndicationLevel.ERROR,
    IndicationKind.ACTION_REJECTED: IndicationLevel.ERROR,
    IndicationKind.ACTION_LONG_RUNNING: IndicationLevel.LONG_RUNNING,
    IndicationKind.STT_ERROR: IndicationLevel.ERROR,
    IndicationKind.TTS_FAILURE: IndicationLevel.ERROR,
    IndicationKind.AUDIO_DEVICE_LOST: IndicationLevel.ERROR,
    # Tier 2 mostly info/success
    IndicationKind.INTENT_SUBMITTED: IndicationLevel.INFO,
    IndicationKind.INTENT_COMPLETED: IndicationLevel.SUCCESS,
    IndicationKind.INTENT_CANCELLED: IndicationLevel.INFO,
    IndicationKind.WAKE_WORD_DETECTED: IndicationLevel.INFO,
    IndicationKind.MODE_CHANGED: IndicationLevel.INFO,
    IndicationKind.DAEMON_STARTED: IndicationLevel.SUCCESS,
    IndicationKind.DAEMON_SHUTDOWN: IndicationLevel.INFO,
    IndicationKind.CONFIRMATION_TIMED_OUT: IndicationLevel.INFO,
    IndicationKind.MCP_DISCONNECTED: IndicationLevel.ERROR,
    IndicationKind.WORKFLOW_SAVED: IndicationLevel.SUCCESS,
    IndicationKind.WORKFLOW_RUN_START: IndicationLevel.INFO,
    IndicationKind.WORKFLOW_RUN_END: IndicationLevel.SUCCESS,
    # Tier 3 info
    IndicationKind.MULTI_INTENT_START: IndicationLevel.INFO,
    IndicationKind.MULTI_INTENT_END: IndicationLevel.INFO,
    IndicationKind.NETWORK_UNREACHABLE: IndicationLevel.ERROR,
    IndicationKind.HEARTBEAT_TICK: IndicationLevel.INFO,
    IndicationKind.NEW_CONVERSATION: IndicationLevel.INFO,
}


_DEFAULTS: dict[IndicationKind, tuple[str, str]] = {
    IndicationKind.AWAITING_CONFIRMATION: ("heare: confirm?", "Say 'гава так' or 'гава ні' (30s)"),
    IndicationKind.CONFIRMATION_DEADLINE: ("heare: 5s left", "Confirmation will expire"),
    IndicationKind.CONFIRMATION_TIMED_OUT: ("heare: timeout", "Confirmation window closed"),
    IndicationKind.MCP_AUTH_REQUIRED: ("heare: MCP auth needed", "MCP server needs authentication — check terminal"),
    IndicationKind.ACTION_FAILED: ("heare: action failed", ""),
    IndicationKind.ACTION_REJECTED: ("heare: action rejected", ""),
    IndicationKind.ACTION_LONG_RUNNING: ("heare: long action", ""),
    IndicationKind.STT_ERROR: ("heare: STT error", ""),
    IndicationKind.TTS_FAILURE: ("heare: TTS failure", ""),
    IndicationKind.AUDIO_DEVICE_LOST: ("heare: audio lost", "Mic or speaker disconnected"),
    IndicationKind.INTENT_SUBMITTED: ("heare: queued", ""),
    IndicationKind.INTENT_COMPLETED: ("heare: done", ""),
    IndicationKind.INTENT_CANCELLED: ("heare: cancelled", ""),
    IndicationKind.WAKE_WORD_DETECTED: ("heare: wake", ""),
    IndicationKind.MODE_CHANGED: ("heare: mode change", ""),
    IndicationKind.DAEMON_STARTED: ("heare: ready", "Listening"),
    IndicationKind.DAEMON_SHUTDOWN: ("heare: shutdown", "Daemon stopping"),
    IndicationKind.MCP_DISCONNECTED: ("heare: MCP disconnect", ""),
    IndicationKind.WORKFLOW_SAVED: ("heare: workflow saved", ""),
    IndicationKind.WORKFLOW_RUN_START: ("heare: workflow start", ""),
    IndicationKind.WORKFLOW_RUN_END: ("heare: workflow done", ""),
    IndicationKind.MULTI_INTENT_START: ("heare: chain start", ""),
    IndicationKind.MULTI_INTENT_END: ("heare: chain end", ""),
    IndicationKind.NETWORK_UNREACHABLE: ("heare: network down", ""),
    IndicationKind.HEARTBEAT_TICK: ("heare: heartbeat", ""),
    IndicationKind.NEW_CONVERSATION: ("heare: new conversation", ""),
}


class Backend(Protocol):
    name: str

    async def fire(
        self,
        kind: IndicationKind,
        level: IndicationLevel,
        title: str,
        body: str,
        meta: dict,
    ) -> None: ...

    async def aclose(self) -> None: ...


def _is_within_quiet_hours(now: _dt.time, ranges: list[tuple[str, str]]) -> bool:
    for start_s, end_s in ranges:
        try:
            start = _dt.time.fromisoformat(start_s)
            end = _dt.time.fromisoformat(end_s)
        except ValueError:
            continue
        if start <= end:
            if start <= now < end:
                return True
        else:
            # Wraps midnight: e.g. 22:00 → 07:00
            if now >= start or now < end:
                return True
    return False


class Indication:
    """Sync, thread-safe facade. Construct on the main asyncio loop.

    Producers call `notify(kind, ...)` from any context (async or sync,
    main loop or worker thread). The facade enqueues a dispatch coroutine
    onto the captured loop and returns immediately.
    """

    def __init__(
        self,
        settings: IndicationSettings,
        backends: list[Backend],
        *,
        loop: asyncio.AbstractEventLoop | None = None,
        mode_provider: Callable[[], Mode] | None = None,
        clock: Callable[[], float] | None = None,
        wallclock: Callable[[], _dt.datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._backends = list(backends)
        self._loop = loop or asyncio.get_running_loop()
        self._mode_provider = mode_provider or (lambda: Mode.AMBIENT)
        self._clock = clock or time.monotonic
        self._wallclock = wallclock or _dt.datetime.now
        self._enabled = settings.enabled
        self._cooldown_state: dict[IndicationKind, float] = {}
        self._lock = asyncio.Lock()
        # Track tasks so reload() can drain them without races.
        self._inflight: set[asyncio.Task] = set()

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def notify(
        self,
        kind: IndicationKind,
        *,
        title: str | None = None,
        body: str | None = None,
        meta: dict | None = None,
    ) -> None:
        """Sync, thread-safe, fire-and-forget. Never raises."""
        try:
            if not self._enabled:
                return
            level = KIND_TO_LEVEL.get(kind)
            if level is None:
                logger.warning("indication.notify: unknown kind %r", kind)
                return
            # Cooldown coalescing — same kind within window is dropped.
            now_mono = self._clock()
            last = self._cooldown_state.get(kind)
            if last is not None and now_mono - last < self._settings.cooldown_seconds:
                return
            self._cooldown_state[kind] = now_mono

            t, b = _DEFAULTS.get(kind, ("heare", ""))
            title = title if title is not None else t
            body = body if body is not None else b

            coro = self._dispatch(kind, level, title, body, meta or {})
            self._submit(coro)
        except Exception:
            logger.exception("indication.notify raised — swallowed")

    def _submit(self, coro: Awaitable[None]) -> None:
        if self._loop.is_closed():
            try:
                coro.close()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
            logger.debug("indication.notify: loop closed; dropping")
            return
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is self._loop:
            self._loop.call_soon_threadsafe(self._spawn_on_loop, coro)
        else:
            asyncio.run_coroutine_threadsafe(self._dispatch_off_loop(coro), self._loop)

    def _spawn_on_loop(self, coro: Awaitable[None]) -> None:
        task = self._loop.create_task(coro)  # type: ignore[arg-type]
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    async def _dispatch_off_loop(self, coro: Awaitable[None]) -> None:
        # Wraps the inner dispatch so run_coroutine_threadsafe always has a
        # coroutine to schedule, and so we can track it via _inflight.
        task = asyncio.current_task()
        if task is not None:
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)
        await coro  # type: ignore[misc]

    async def _dispatch(
        self,
        kind: IndicationKind,
        level: IndicationLevel,
        title: str,
        body: str,
        meta: dict,
    ) -> None:
        # Per-kind toggle look-up; defaults already loaded by config.
        kind_toggle = self._settings.kinds.get(level.value)
        if kind_toggle is None:
            return
        mode = self._mode_provider()
        sound_allowed_by_mode = self._mode_allows_sound(mode, level)
        in_quiet = _is_within_quiet_hours(
            self._wallclock().time(), self._settings.quiet_hours
        )

        targets: list[tuple[Backend, str]] = []
        for backend in self._backends:
            name = backend.name
            if name == "sound":
                if not (
                    self._settings.sound_enabled
                    and kind_toggle.sound
                    and sound_allowed_by_mode
                    and not in_quiet
                ):
                    continue
            elif name == "visual":
                if not (self._settings.visual_enabled and kind_toggle.visual):
                    continue
            elif name == "notification":
                # INPUT_WAITING bypasses quiet hours per plan.
                if not (
                    self._settings.notification_center_enabled
                    and kind_toggle.notification
                ):
                    continue
                if in_quiet and level is not IndicationLevel.INPUT_WAITING:
                    continue
            else:
                # Unknown backend name — fire unconditionally if registered.
                pass
            targets.append((backend, name))

        for backend, name in targets:
            try:
                await backend.fire(kind, level, title, body, meta)
            except Exception:
                logger.warning(
                    "indication backend %s.fire(%s) raised; ignoring",
                    name,
                    kind.value,
                    exc_info=True,
                )

    @staticmethod
    def _mode_allows_sound(mode: Any, level: IndicationLevel) -> bool:
        """Whether *level* may chime under the active mode.

        Driven by the mode's ``sound_policy`` (single source of truth in
        src/agent/modes.py): ``None`` = allow all, a set = allow only
        those IndicationLevel values, empty set = silent. Accepts a
        ModeProfile, a Mode enum, or a mode-name str (the latter two are
        resolved through the registry) so legacy callers/tests that pass
        ``Mode.SILENT`` keep their exact prior behavior.
        """
        from src.agent.modes import ModeProfile, resolve

        if isinstance(mode, ModeProfile):
            profile = mode
        else:
            name = getattr(mode, "value", mode)
            profile = resolve(str(name))
        policy = profile.sound_policy
        if policy is None:
            return True
        return level.value in policy

    async def aclose(self) -> None:
        """Close every backend; best-effort, never raises. Idempotent."""
        if not self._enabled and not self._backends:
            return
        self._enabled = False
        # Drain currently-scheduled tasks (bounded).
        if self._inflight:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*list(self._inflight), return_exceptions=True),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                logger.warning("indication: drain timed out at aclose")
        for b in self._backends:
            try:
                await b.aclose()
            except Exception:
                logger.warning(
                    "indication backend %s.aclose raised; ignoring",
                    getattr(b, "name", "?"),
                    exc_info=True,
                )

    async def reload(
        self,
        settings: IndicationSettings,
        *,
        new_backends: list[Backend] | None = None,
    ) -> None:
        """Runtime disable/re-enable. See plan §2.1 for ordering rationale.

        Sequence:
          1. acquire lock; set self._enabled=False
          2. release lock
          3. drain pending dispatch tasks (bounded)
          4. aclose() each backend
          5. install new settings + (optionally) new backends
          6. if settings.enabled, flip self._enabled=True

        Any notify() arriving between steps 1 and 6 sees _enabled=False under
        the lock and short-circuits — it never enqueues into a closing backend.
        """
        async with self._lock:
            self._enabled = False
        if self._inflight:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*list(self._inflight), return_exceptions=True),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                logger.warning("indication.reload: drain timed out")
        for b in self._backends:
            try:
                await b.aclose()
            except Exception:
                logger.warning(
                    "indication.reload: backend %s.aclose raised; ignoring",
                    getattr(b, "name", "?"),
                    exc_info=True,
                )
        self._settings = settings
        self._cooldown_state.clear()
        if new_backends is not None:
            self._backends = list(new_backends)
        if settings.enabled:
            self._enabled = True


# ---------------------------------------------------------------------------
# SoundCueProcessor — pipecat FrameProcessor that brackets each cue with
# IndicationCueFrame(start=True/False) and emits OutputAudioRawFrame in
# between. Built lazily so admin paths importing src.voice.indication.core for the
# enum/facade don't pay pipecat-FrameProcessor import cost up front.
# ---------------------------------------------------------------------------

_sound_cue_processor_cls: type | None = None


def _build_sound_cue_processor_class() -> type:
    global _sound_cue_processor_cls
    if _sound_cue_processor_cls is not None:
        return _sound_cue_processor_cls

    from pipecat.frames.frames import (  # type: ignore
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        OutputAudioRawFrame,
    )
    from pipecat.processors.frame_processor import (  # type: ignore
        FrameDirection,
        FrameProcessor,
    )

    class SoundCueProcessor(FrameProcessor):  # type: ignore[misc,valid-type]
        """Per cue, pushes IndicationCueFrame(start=True), OutputAudioRawFrame,
        then IndicationCueFrame(start=False) at len(pcm)/(sr*2)+0.2s later.

        Defers cue emission while bot is speaking; drops cue after 2s wait.
        Buffers up to one pending cue (asyncio.Queue(maxsize=1)); subsequent
        cues are dropped and counted via `cues_dropped`.
        """

        def __init__(self, sample_rate: int = 24000, tail_pad_seconds: float = 0.2) -> None:
            super().__init__()
            self._sample_rate = sample_rate
            self._tail_pad = tail_pad_seconds
            self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)
            self._drain_task: asyncio.Task | None = None
            self._stopped = False
            self._bot_speaking = False
            self.cues_dropped = 0

        @property
        def sample_rate(self) -> int:
            return self._sample_rate

        def enqueue_cue(self, pcm: bytes) -> None:
            if self._stopped:
                return
            if self._drain_task is None:
                try:
                    loop = asyncio.get_running_loop()
                    self._drain_task = loop.create_task(self._drain())
                except RuntimeError:
                    logger.debug("sound cue: no running loop; dropping")
                    return
            try:
                self._queue.put_nowait(pcm)
            except asyncio.QueueFull:
                self.cues_dropped += 1
                logger.debug(
                    "sound cue dropped (queue full); cues_dropped=%d",
                    self.cues_dropped,
                )

        async def _drain(self) -> None:
            while not self._stopped:
                try:
                    pcm = await self._queue.get()
                except asyncio.CancelledError:
                    return
                if self._stopped:
                    return
                # Defer while bot speaks; drop after 2s wait.
                deadline = time.monotonic() + 2.0
                while self._bot_speaking and time.monotonic() < deadline:
                    await asyncio.sleep(0.05)
                if self._bot_speaking:
                    self.cues_dropped += 1
                    continue
                try:
                    await self.push_frame(
                        IndicationCueFrame(start=True), FrameDirection.DOWNSTREAM
                    )
                    await self.push_frame(
                        OutputAudioRawFrame(
                            audio=pcm,
                            sample_rate=self._sample_rate,
                            num_channels=1,
                        ),
                        FrameDirection.DOWNSTREAM,
                    )
                    tail_s = len(pcm) / (self._sample_rate * 2) + self._tail_pad
                    await asyncio.sleep(tail_s)
                    await self.push_frame(
                        IndicationCueFrame(start=False), FrameDirection.DOWNSTREAM
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("sound cue drain push raised", exc_info=True)

        async def process_frame(self, frame, direction) -> None:  # type: ignore[override]
            await super().process_frame(frame, direction)
            if isinstance(frame, BotStartedSpeakingFrame):
                self._bot_speaking = True
            elif isinstance(frame, BotStoppedSpeakingFrame):
                self._bot_speaking = False
            await self.push_frame(frame, direction)

        async def aclose(self) -> None:
            self._stopped = True
            if self._drain_task is not None and not self._drain_task.done():
                self._drain_task.cancel()
                try:
                    await asyncio.wait_for(self._drain_task, timeout=1.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass

    _sound_cue_processor_cls = SoundCueProcessor
    return _sound_cue_processor_cls


def build_sound_cue_processor(
    sample_rate: int = 24000, tail_pad_seconds: float = 0.2
):
    """Factory for SoundCueProcessor — lazy-imports pipecat.

    Returns an instance of the SoundCueProcessor class, suitable for
    insertion in the pipecat pipeline AFTER the tts processor and BEFORE
    transport.output().
    """
    cls = _build_sound_cue_processor_class()
    return cls(sample_rate=sample_rate, tail_pad_seconds=tail_pad_seconds)


# Process-wide singleton — set by pipeline.py at startup so producers in
# any module can fire notify() without threading the facade through call
# sites. Producers must tolerate `None` (indication subsystem disabled or
# not yet constructed) and call notify() conditionally:
#
#     ind = get_indication()
#     if ind is not None:
#         ind.notify(IndicationKind.X, body="...")
#
# Tests can call set_indication(None) in fixtures to ensure isolation.
_INSTANCE: "Indication | None" = None


def set_indication(indication: "Indication | None") -> None:
    global _INSTANCE
    _INSTANCE = indication


def get_indication() -> "Indication | None":
    return _INSTANCE


__all__ = [
    "Backend",
    "IndicationCueFrame",
    "IndicationKind",
    "IndicationLevel",
    "Indication",
    "KIND_TO_LEVEL",
    "_DEFAULTS",
    "_is_within_quiet_hours",
    "build_sound_cue_processor",
    "set_indication",
    "get_indication",
]
