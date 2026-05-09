#!/usr/bin/env python3
"""Record a few seconds from the mic and classify with YAMNet.

Usage:
    uv run python scripts/yamnet_mic.py [--duration 4] [--top-k 5] [--allowlist-only]

The script records a ``--duration``-second clip from the default input
device at 16 kHz mono, slides 0.96 s windows with 0.48 s hop over it, and
prints the top-K YAMNet predictions per window. ``--allowlist-only``
filters to the 17 curated dashboard labels (Laughter, Bark, Cough, …).

Pure offline test of the model + preprocessing pipeline — does not
involve the daemon, the watch dashboard, or ``audio_event.json``.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

from src.audio_event.class_map import AUDIOSET_CLASSES, label_for_index
from src.audio_event.classifier import YamnetClassifier


def record(duration_s: float, sample_rate: int = 16000) -> np.ndarray:
    """Capture ``duration_s`` seconds of mono int16 PCM, return float32 in [-1, 1]."""
    try:
        import pyaudio
    except ImportError:
        sys.exit(
            "pyaudio not installed — install heare's local audio extra: "
            "uv pip install -e '.[local]'"
        )

    p = pyaudio.PyAudio()
    stream = p.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=sample_rate,
        input=True,
        frames_per_buffer=1024,
    )
    print(f"recording {duration_s:.1f}s... (make a sound now)")
    chunks = []
    n_frames = int(sample_rate * duration_s / 1024) + 1
    for _ in range(n_frames):
        chunks.append(stream.read(1024, exception_on_overflow=False))
    stream.stop_stream()
    stream.close()
    p.terminate()
    raw = np.frombuffer(b"".join(chunks), dtype=np.int16)
    return (raw.astype(np.float32) / 32768.0)[: int(sample_rate * duration_s)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=4.0)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--allowlist-only", action="store_true")
    parser.add_argument(
        "--model",
        type=Path,
        default=Path.home() / ".heare" / "models" / "yamnet.onnx",
    )
    args = parser.parse_args()

    classifier = YamnetClassifier(args.model)
    waveform = record(args.duration)

    win = YamnetClassifier.WINDOW_SAMPLES  # 15360 = 0.96 s
    hop = win // 2  # 0.48 s
    if len(waveform) < win:
        sys.exit(f"need at least 0.96s of audio (got {len(waveform) / 16000:.2f}s)")

    print(f"\nclassifying {len(waveform) / 16000:.2f}s "
          f"({1 + (len(waveform) - win) // hop} windows)\n")
    t0 = time.perf_counter()
    for start in range(0, len(waveform) - win + 1, hop):
        window = waveform[start : start + win]
        preds = classifier.classify(window)
        if args.allowlist_only:
            preds = [(idx, s) for idx, s in preds if label_for_index(idx)]
        print(f"-- t={start / 16000:5.2f}s ---")
        for idx, score in preds[: args.top_k]:
            tag = label_for_index(idx) or AUDIOSET_CLASSES[idx]
            bar = "█" * int(score * 30)
            print(f"  {score:.3f}  {bar:<30}  {tag}")
    print(f"\ntotal inference: {(time.perf_counter() - t0) * 1000:.0f} ms")


if __name__ == "__main__":
    main()
