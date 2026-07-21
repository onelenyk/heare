"""Gain control stages for mic input and speaker output.

InputGainProcessor multiplies InputAudioRawFrame samples by a gain
factor read from state (``input_gain`` key) with a settings fallback.
OutputVolumeProcessor multiplies TTSAudioRawFrame samples by a volume
factor read from state (``output_volume`` key) with a settings fallback.

Both support hot-reload: the agent tools ``mic_gain`` and ``volume``
write the respective state keys, and the processors pick up the new
value on the next frame.  Values are validated to be in [0.0, 5.0].
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.config import Settings
    from src.state import State

logger = logging.getLogger("heare.gain_control")

_input_gain_cls: type | None = None
_output_volume_cls: type | None = None


def _build_input_gain_class(settings: "Settings", state: "State"):
    global _input_gain_cls
    if _input_gain_cls is not None:
        return _input_gain_cls

    import numpy as np
    from pipecat.frames.frames import InputAudioRawFrame
    from pipecat.processors.frame_processor import FrameProcessor

    class InputGainProcessor(FrameProcessor):  # type: ignore[misc,valid-type]
        def __init__(self) -> None:
            super().__init__()
            self._settings = settings
            self._state = state

        def _current_gain(self) -> float:
            raw = self._state.get("input_gain")
            if raw:
                try:
                    return float(raw)
                except ValueError:
                    pass
            return self._settings.input_gain

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, InputAudioRawFrame):
                gain = self._current_gain()
                if gain != 1.0 and frame.audio:
                    audio_np = np.frombuffer(
                        frame.audio, dtype=np.int16
                    ).astype(np.float32)
                    audio_np = np.clip(
                        audio_np * gain, -32768, 32767
                    ).astype(np.int16)
                    frame.audio = audio_np.tobytes()
            await self.push_frame(frame, direction)

    _input_gain_cls = InputGainProcessor
    return _input_gain_cls


def _build_output_volume_class(settings: "Settings", state: "State"):
    global _output_volume_cls
    if _output_volume_cls is not None:
        return _output_volume_cls

    import numpy as np
    from pipecat.frames.frames import TTSAudioRawFrame
    from pipecat.processors.frame_processor import FrameProcessor

    class OutputVolumeProcessor(FrameProcessor):  # type: ignore[misc,valid-type]
        def __init__(self) -> None:
            super().__init__()
            self._settings = settings
            self._state = state

        def _current_volume(self) -> float:
            raw = self._state.get("output_volume")
            if raw:
                try:
                    return float(raw)
                except ValueError:
                    pass
            return self._settings.output_volume

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, TTSAudioRawFrame):
                volume = self._current_volume()
                if volume != 1.0 and frame.audio:
                    audio_np = np.frombuffer(
                        frame.audio, dtype=np.int16
                    ).astype(np.float32)
                    audio_np = np.clip(
                        audio_np * volume, -32768, 32767
                    ).astype(np.int16)
                    frame.audio = audio_np.tobytes()
            await self.push_frame(frame, direction)

    _output_volume_cls = OutputVolumeProcessor
    return _output_volume_cls


def create_input_gain_processor(*, settings: "Settings", state: "State") -> Any:
    cls = _build_input_gain_class(settings, state)
    return cls()


def create_output_volume_processor(*, settings: "Settings", state: "State") -> Any:
    cls = _build_output_volume_class(settings, state)
    return cls()


__all__ = [
    "create_input_gain_processor",
    "create_output_volume_processor",
]
