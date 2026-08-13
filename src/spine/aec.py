"""Echo cancellation for the pipecat-free spine.

The daemon already has a working canceller — ``src/core/aec.py`` — fixed
after an int16/float overflow bug turned it into a mute (40-50 dB of real
suppression once fixed; see ``docs/findings/echo-cancellation.md``). Its
adaptive-filter logic (``AudioProcessor`` from ``pywebrtc-audio``, WebRTC's
AEC3) and its far-end reference buffer (``FarEnd``) do not import pipecat
at module scope — only the two factory functions that wrap them into
pipecat frame processors do, and this module never calls those. So the
buffer is reused directly by import; the AEC3 call sequence (int16 <->
[-1, 1] scaling, 10 ms native block size, reference drained in lockstep
with the near end) is adapted here for the spine's synchronous
``process()`` contract instead of pipecat's async frame stream.

Shapes: mic frames are 16 kHz mono int16, 20 ms (640 bytes) — two AEC3
native 10 ms blocks per frame. Speaker audio is 24 kHz mono int16 in
whatever chunk size ``play()`` was given. ``FarEnd.push`` already resamples
whatever it is handed onto the 16 kHz mic timeline (linear interpolation,
same as ``src/pipeline/build.py``'s far-end collector feeds it), and
``FarEnd.take`` drains it one reference block at a time, zero-padding once
playback has drained — exactly the alignment ``process()`` needs.
"""

from __future__ import annotations

import logging

import numpy as np

from src.core.aec import FarEnd

logger = logging.getLogger(__name__)

# AEC3's native block size is fixed at 10 ms regardless of frame_ms the
# caller chunks mic audio into.
_BLOCK_MS = 10

# Best-effort estimate of speaker-to-microphone delay. src/core/aec.py's
# DelayEstimator exists to measure this precisely from a live cross-
# correlation; the spine does not (yet) wire that in, so a stream-delay
# guess is used the same way the daemon's default configuration does.
_STREAM_DELAY_MS = 30


class SpineAEC:
    """Echo canceller for full duplex: mic frames cleaned against what
    the speaker is playing."""

    def __init__(self, rate: int = 16000, far_rate: int = 24000) -> None:
        self._rate = rate
        self._far_rate = far_rate
        self._block = max(1, rate * _BLOCK_MS // 1000)

        # FarEnd resamples onto a hardcoded 16 kHz timeline (it backs the
        # daemon's own mic rate). Reused as-is since the spine's default
        # matches; a spine running at a different `rate` would need its
        # own buffer.
        self._far = FarEnd()

        self._apm = None
        self._active = False
        try:
            from pywebrtc_audio import AudioProcessor

            self._apm = AudioProcessor(
                sample_rate=rate,
                num_channels=1,
                echo_cancellation=True,
                # Off, unlike src/core/aec.py's default: WebRTC's noise
                # suppressor treats a steady tone as stationary noise and
                # attenuates it by ~12 dB even with a silent far end,
                # which fights the "no echo -> essentially unchanged"
                # contract. Echo cancellation alone is what this class
                # promises.
                noise_suppression=False,
                auto_gain_control=False,
                stream_delay_ms=_STREAM_DELAY_MS,
            )
            self._active = True
        except Exception:
            logger.info("spine.aec: pywebrtc_audio unavailable, passthrough")
            self._apm = None
            self._active = False

    def push_far(self, pcm: bytes) -> None:
        """Speaker audio (far_rate mono int16), any chunk size — called
        from the same thread/loop as play()."""
        if not pcm:
            return
        try:
            # Even byte count only: an odd trailing byte would otherwise
            # raise inside np.frombuffer(dtype=int16).
            usable = len(pcm) - (len(pcm) % 2)
            if usable <= 0:
                return
            self._far.push(pcm[:usable], self._far_rate)
        except Exception:
            logger.debug("spine.aec: push_far failed on malformed chunk", exc_info=True)

    def clear(self) -> None:
        """Drop the queued far-end reference.

        Cancelled audio is never played, so it must not stay in the
        reference — otherwise AEC3 spends the next seconds subtracting
        an echo no speaker ever produced (the daemon's collector does
        the same on InterruptionFrame).
        """
        try:
            self._far.clear()
        except Exception:
            pass

    def process(self, frame: bytes) -> bytes:
        """One 20 ms mic frame in, echo-cancelled frame of the SAME
        length out. Must never block beyond ~1ms budget; if the canceller
        is unavailable or fails, return the frame unchanged (fail-open)."""
        if not self._active or self._apm is None:
            return frame

        try:
            samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
            n = samples.size
            block = self._block
            chunks = []
            i = 0
            while i + block <= n:
                near = samples[i : i + block]
                far = self._far.take(block)

                # AudioProcessor works in [-1, 1]; handed int16-scaled
                # values it clips everything to +-1.0 (amplitude 1 of
                # 32768 -> -90 dB), which is the overflow bug this module
                # inherits the fix for. Scale down going in, back up
                # coming out.
                cleaned = (
                    np.asarray(
                        self._apm.process(near / 32768.0, far / 32768.0),
                        dtype=np.float32,
                    )
                    * 32768.0
                )
                chunks.append(cleaned)
                i += block

            # A remainder shorter than one 10 ms block (only possible if
            # the caller hands a frame that isn't a whole multiple of the
            # native block size) is passed through unprocessed so the
            # output length always matches the input length exactly.
            if i < n:
                chunks.append(samples[i:])

            out = np.concatenate(chunks) if chunks else samples
            result = np.clip(out, -32768, 32767).astype(np.int16).tobytes()

            if len(result) != len(frame):
                return frame
            return result
        except Exception:
            logger.debug("spine.aec: process() failed, passthrough", exc_info=True)
            return frame

    @property
    def active(self) -> bool:
        return self._active
