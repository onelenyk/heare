"""Pipecat ``FrameProcessor`` that runs YAMNet inference off the audio loop.

Frame contract
--------------

* Accepts ``InputAudioRawFrame`` carrying int16 mono PCM at 16 kHz —
  the same format the daemon's input transport produces.
* Pass-through: every frame is forwarded downstream unchanged. The
  observer never swallows or re-orders frames.
* When the internal buffer has accumulated one full YAMNet window
  (15 360 samples ≈ 0.96 s) and no inference is currently running,
  the window is shipped to a worker thread via
  ``asyncio.create_task(asyncio.to_thread(self._infer, ...))`` and the
  ``add_done_callback`` finalises bookkeeping back on the event loop.
* Drop policy: while ``_running`` is True, freshly-completed windows
  are discarded. The audio loop never queues work; richer per-event
  metadata is out of scope per the consensus plan.

Confirmation rule
-----------------

A window's top-allowlisted prediction must clear the threshold AND
match the previous window's label before the event fires. This kills
single-window false positives at the cost of ~1 window of detection
latency for sustained events.

Failure semantics
-----------------

* ``_on_result`` always resets ``_running`` in a ``finally`` block, so
  an exception inside ``_infer`` cannot deadlock the observer.
* The factory ``create_audio_event_observer`` returns ``None`` on
  every recoverable failure (feature disabled, ``onnxruntime`` missing,
  model file missing). The pipeline builder treats ``None`` as "skip
  this stage" — see :mod:`src.pipeline.build`.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from pipecat.frames.frames import InputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from src.audio_event.class_map import label_for_index
from src.audio_event.writer import write_audio_event

if TYPE_CHECKING:
    from src.audio_event.classifier import YamnetClassifier
    from src.config import Settings


logger = logging.getLogger("heare.audio_event")


class AudioEventObserver(FrameProcessor):
    """Buffer mic audio, classify in a worker thread, emit confirmed events."""

    WINDOW_SAMPLES = 15360  # 0.96 s × 16 kHz
    WINDOW_BYTES = WINDOW_SAMPLES * 2  # int16

    def __init__(
        self,
        classifier: "YamnetClassifier",
        state_file: Path,
        *,
        threshold: float = 0.4,
    ) -> None:
        super().__init__()
        self._classifier = classifier
        self._state_file = state_file
        self._threshold = threshold
        self._buf = bytearray()
        self._running: bool = False
        self._prev_label: Optional[str] = None

    async def process_frame(self, frame, direction: FrameDirection) -> None:  # type: ignore[override]
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            self._buf.extend(frame.audio)
            if len(self._buf) >= self.WINDOW_BYTES:
                if self._running:
                    # Drop the window — inference is still busy.
                    del self._buf[: self.WINDOW_BYTES]
                else:
                    window_bytes = bytes(self._buf[: self.WINDOW_BYTES])
                    del self._buf[: self.WINDOW_BYTES]
                    self._running = True
                    waveform = self._bytes_to_waveform(window_bytes)
                    task = asyncio.create_task(
                        asyncio.to_thread(self._infer, waveform)
                    )
                    task.add_done_callback(self._on_result)
        await self.push_frame(frame, direction)

    @staticmethod
    def _bytes_to_waveform(window_bytes: bytes):
        import numpy as np

        ints = np.frombuffer(window_bytes, dtype=np.int16)
        return (ints.astype(np.float32) / 32768.0)

    def _infer(self, waveform) -> Optional[tuple[str, float]]:
        """Synchronous inference — runs on the worker thread."""
        scores = self._classifier.classify(waveform)
        for idx, score in scores:
            if score < self._threshold:
                # Sorted descending — nothing else can clear the bar.
                return None
            label = label_for_index(idx)
            if label is not None:
                return (label, score)
        return None

    def _on_result(self, fut: "asyncio.Future") -> None:
        """Done-callback executed on the event loop. Always resets ``_running``."""
        try:
            result = fut.result()
        except Exception:  # noqa: BLE001 — ONNX failures must not crash the loop
            logger.exception("audio_event: classifier raised; dropping window")
            self._prev_label = None
            return
        else:
            if result is not None:
                label, score = result
                if label == self._prev_label:
                    write_audio_event(self._state_file, label, score)
                    logger.info("audio_event: %s (%.2f)", label, score)
                self._prev_label = label
            else:
                self._prev_label = None
        finally:
            self._running = False


def create_audio_event_observer(settings: "Settings") -> Optional[AudioEventObserver]:
    """Construct the observer, or return ``None`` if the feature is disabled
    or any prerequisite is missing.

    Three documented "skip" paths, each logged at WARNING:
      * feature flag off (silent — already the no-op default)
      * ``onnxruntime`` not importable (optional dep group not installed)
      * model file missing at ``settings.yamnet_model_path``
    """
    if not settings.audio_event_detection_enabled:
        return None
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        logger.warning(
            "audio_event: onnxruntime not installed — install heare[audio-event]"
        )
        return None
    if not settings.yamnet_model_path.exists():
        logger.warning(
            "audio_event: model not found at %s — see classifier.py docstring "
            "for the tf2onnx conversion recipe",
            settings.yamnet_model_path,
        )
        return None
    from src.audio_event.classifier import YamnetClassifier

    classifier = YamnetClassifier(settings.yamnet_model_path)
    return AudioEventObserver(
        classifier,
        settings.audio_event_file,
        threshold=settings.audio_event_threshold,
    )


__all__ = ["AudioEventObserver", "create_audio_event_observer"]
