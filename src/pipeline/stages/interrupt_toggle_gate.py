"""Interrupt-toggle gate — state-driven barge-in guard.

When the interrupt toggle is OFF (``interrupt_off`` key in State is "1"),
mic input that arrives while the bot is speaking is dropped — the bot
finishes its current utterance before the user can speak. When the toggle
is ON, the default open-mic barge-in behaviour is preserved.

Sits in the input path after ``input_mute_gate`` so muted mic audio is
already filtered before this gate runs.

Reads from State's in-memory cache (sync dict lookup, no filesystem call)
on every frame. The API handler updates State via ``set_bool()``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.state import State

logger = logging.getLogger("heare.interrupt_toggle_gate")


_gate_cls: type | None = None


def _build_interrupt_gate_class(state: "State"):
    global _gate_cls
    if _gate_cls is not None:
        return _gate_cls

    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        InputAudioRawFrame,
    )
    from pipecat.processors.frame_processor import FrameProcessor

    class InterruptToggleGateProcessor(FrameProcessor):  # type: ignore[misc,valid-type]
        def __init__(self) -> None:
            super().__init__()
            self._bot_speaking = False
            self._log_toggle = True
            self._state = state

        def _interrupt_off(self) -> bool:
            """Check if interrupt is disabled via State cache (no filesystem call)."""
            return self._state.get_bool("interrupt_off")

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)

            if isinstance(frame, BotStartedSpeakingFrame):
                self._bot_speaking = True
                if self._interrupt_off():
                    if self._log_toggle:
                        logger.info(
                            "interrupt_toggle: dropping mic input while bot speaks (interrupt disabled)"
                        )
                        self._log_toggle = False
                await self.push_frame(frame, direction)
                return

            if isinstance(frame, BotStoppedSpeakingFrame):
                self._bot_speaking = False
                self._log_toggle = True
                if self._interrupt_off():
                    logger.debug(
                        "interrupt_toggle: bot stopped speaking — mic input resumes"
                    )
                await self.push_frame(frame, direction)
                return

            if isinstance(frame, InputAudioRawFrame):
                if self._bot_speaking and self._interrupt_off():
                    return

            await self.push_frame(frame, direction)

    _gate_cls = InterruptToggleGateProcessor
    return _gate_cls


def create_interrupt_toggle_gate(*, state: "State") -> Any:
    cls = _build_interrupt_gate_class(state)
    return cls()


__all__ = ["create_interrupt_toggle_gate"]
