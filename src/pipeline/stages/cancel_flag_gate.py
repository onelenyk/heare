"""Cancel-flag gate — external "interrupt now" trigger.

Symmetric with :mod:`src.pipeline.stages.mute_gate`: a flag file at
 ``settings.cancel_flag_file`` acts as the contract between any outside
process (watch dashboard, hotkey daemon) and the running
Pipecat pipeline.

Lifecycle
---------

* On every frame the gate checks ``flag_path.exists()``. If False, the
  frame is forwarded unchanged.
* If True, the gate deletes the flag (so the next frame does not
  re-trigger), pushes a single ``InterruptionFrame`` upstream — the same
  payload :class:`TranscriptionGate` emits on a spoken stop-word — then
  still forwards the original frame downstream.

Pipecat routes ``SystemFrame`` subclasses (including ``InterruptionFrame``)
through immediate-delivery channels, so the cancel takes effect inside
one frame-cycle without waiting on per-stage queues.

The Pipecat import is deferred inside ``_build_cancel_flag_gate_class``
so the admin CLI paths (which transitively import this module) keep
working on machines without portaudio.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any


logger = logging.getLogger("heare.cancel_flag_gate")


_gate_cls: type | None = None


def _build_cancel_flag_gate_class():
    global _gate_cls
    if _gate_cls is not None:
        return _gate_cls

    from pipecat.frames.frames import InterruptionFrame
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    class CancelFlagGate(FrameProcessor):  # type: ignore[misc,valid-type]
        def __init__(self, *, state) -> None:
            super().__init__()
            self._state = state

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)
            if self._state.get_bool("cancel"):
                # Clear after read so next frame doesn't re-trigger
                await self._state.set("cancel", "0")
                # Push downstream so the PipelineTask (with
                # allow_interruptions=True) cancels TTS/LLM/tool calls,
                # and the _TtsFadeOnInterruption observer fires the
                # fade-out. Also push upstream for any upstream-only
                # stages that need to react.
                try:
                    await self.push_frame(
                        InterruptionFrame(), FrameDirection.DOWNSTREAM
                    )
                    logger.info(
                        "cancel_flag_gate: InterruptionFrame pushed downstream"
                    )
                except Exception:
                    logger.exception(
                        "cancel_flag_gate: downstream push failed (non-fatal)"
                    )
            await self.push_frame(frame, direction)

    _gate_cls = CancelFlagGate
    return _gate_cls


def create_cancel_flag_gate(*, state) -> Any:
    cls = _build_cancel_flag_gate_class()
    return cls(state=state)


def request_cancel(flag_path: Path) -> None:
    """External helper — creates the flag so the next pipeline frame fires
    the InterruptionFrame. Idempotent."""
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.touch(exist_ok=True)


__all__ = ["create_cancel_flag_gate", "request_cancel"]
