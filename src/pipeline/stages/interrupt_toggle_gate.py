"""Interrupt-toggle gate — flag-file-driven barge-in guard.

When the interrupt toggle is OFF (``settings.interrupt_enabled_file`` exists),
mic input that arrives while the bot is speaking is dropped — the bot
finishes its current utterance before the user can speak. When the toggle
is ON (file absent), the default open-mic barge-in behaviour is preserved.

Sits in the input path after ``input_mute_gate`` so muted mic audio is
already filtered before this gate runs.

Pipecat imports are deferred so admin CLI paths work without portaudio.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("heare.interrupt_toggle_gate")


_gate_cls: type | None = None


def _build_interrupt_gate_class(interrupt_flag_path):
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

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)

            if isinstance(frame, BotStartedSpeakingFrame):
                self._bot_speaking = True
                if interrupt_flag_path.exists():
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
                if interrupt_flag_path.exists():
                    logger.debug(
                        "interrupt_toggle: bot stopped speaking — mic input resumes"
                    )
                await self.push_frame(frame, direction)
                return

            if isinstance(frame, InputAudioRawFrame):
                if self._bot_speaking and interrupt_flag_path.exists():
                    return

            await self.push_frame(frame, direction)

    _gate_cls = InterruptToggleGateProcessor
    return _gate_cls


def create_interrupt_toggle_gate(*, settings) -> Any:
    cls = _build_interrupt_gate_class(settings.interrupt_enabled_file)
    return cls()


__all__ = ["create_interrupt_toggle_gate"]
