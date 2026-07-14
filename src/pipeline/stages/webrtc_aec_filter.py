"""WebRTC AEC3 acoustic echo cancellation filter.

Wraps ``pywebrtc_audio.AudioProcessor`` (AEC3 + noise suppression) as a
Pipecat-compatible ``FrameProcessor``.  Active only during bot speech or
the cooldown window — AEC3 diverges with an empty reference signal.

Uses the shared ``EchoState`` ring buffer as the far-end (bot output)
reference.  Modifies ``InputAudioRawFrame.audio`` in-place (int16 PCM).
Noise suppression is a bonus side-effect of the WebRTC module.

Pipecat imports are deferred for admin-CLI compatibility.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from src.pipeline.echo_state import EchoState


logger = logging.getLogger("heare.aec_filter")


def _load_pipecat_base():
    from pipecat.frames.frames import Frame, InputAudioRawFrame
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    return (FrameProcessor, FrameDirection, Frame, InputAudioRawFrame)


_filter_cls: type | None = None


def _build_class():
    global _filter_cls
    if _filter_cls is not None:
        return _filter_cls

    (
        FrameProcessor,
        FrameDirection,
        Frame,
        InputAudioRawFrame,
    ) = _load_pipecat_base()

    class WebRTCAECFilter(FrameProcessor):  # type: ignore[misc,valid-type]
        """FrameProcessor wrapping WebRTC AEC3 echo cancellation.

        Parameters
        ----------
        echo_state:
            Shared EchoState ring buffer with recent bot output audio.
        sample_rate:
            Sample rate of the mic audio (and EchoState buffer). Default 16000.
        cooldown_seconds:
            How many seconds after bot stops to keep AEC active.
        stream_delay_ms:
            Fixed delay in ms between reference output and mic capture. 0=auto.
        noise_suppression:
            Enable WebRTC noise suppression in addition to AEC.
        """

        def __init__(
            self,
            echo_state: "EchoState",
            *,
            sample_rate: int = 16000,
            cooldown_seconds: float = 0.5,
            stream_delay_ms: int = 0,
            noise_suppression: bool = True,
        ) -> None:
            super().__init__()
            self._echo_state = echo_state
            self._sample_rate = sample_rate
            self._cooldown = cooldown_seconds
            self._delay_ms = stream_delay_ms
            self._noise_suppression = noise_suppression
            self._processor: Any | None = None

        def _ensure_processor(self) -> Any:
            if self._processor is None:
                from pywebrtc_audio import AudioProcessor as _AP

                self._processor = _AP(
                    sample_rate=self._sample_rate,
                    num_channels=1,
                    echo_cancellation=True,
                    noise_suppression=self._noise_suppression,
                    auto_gain_control=False,
                    stream_delay_ms=self._delay_ms,
                )
            return self._processor

        def _is_active(self) -> bool:
            if self._echo_state.bot_speaking:
                return True
            stopped = self._echo_state.bot_stopped_at
            if stopped > 0 and time.monotonic() - stopped < self._cooldown:
                return True
            return False

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)

            if isinstance(frame, InputAudioRawFrame) and self._is_active():
                mic = np.frombuffer(frame.audio, dtype=np.int16).astype(np.float32)
                ref = self._echo_state.get_buffer()
                min_len = min(len(mic), len(ref))
                if min_len >= 160:  # 10 ms minimum for AEC3
                    ap = self._ensure_processor()
                    cleaned = ap.process(mic[:min_len], ref[:min_len])
                    cleaned = np.clip(cleaned, -32768, 32767).astype(np.int16)
                    if len(mic) > min_len:
                        tail = mic[min_len:].astype(np.int16)
                        cleaned = np.concatenate([cleaned, tail])
                    frame.audio = cleaned.tobytes()

            await self.push_frame(frame, direction)

    _filter_cls = WebRTCAECFilter
    return _filter_cls


def create_aec_filter(echo_state: "EchoState", **kwargs: Any) -> Any:
    cls = _build_class()
    return cls(echo_state, **kwargs)


# Make the class directly importable at module level (eager-init on first access).
if _filter_cls is None:
    _build_class()

WebRTCAECFilter = _filter_cls  # type: ignore[misc]

__all__ = ["create_aec_filter", "WebRTCAECFilter"]
