#!/usr/bin/env python3
"""Probe a WAV file through the YAMNet classifier and print per-window predictions.

Usage:
    uv run python scripts/yamnet_probe.py path/to/audio.wav [--top-k 5] [--allowlist-only]

Reads WAV files via scipy.io.wavfile (already in the heare env). Stereo is
downmixed; non-16 kHz files are linearly resampled. The clip is split into
0.96 s windows with 0.48 s hop (the same overlap the live observer uses).
For each window, prints the top-K predictions; with ``--allowlist-only``
filters to the ~17 curated classes the dashboard surfaces.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from src.audio_event.class_map import AUDIOSET_CLASSES, label_for_index
from src.audio_event.classifier import YamnetClassifier


def load_mono_16k(path: Path) -> np.ndarray:
    """Load a WAV at 16 kHz mono float32 in [-1, 1]."""
    from scipy.io import wavfile

    sr, data = wavfile.read(str(path))
    # Convert int16 / int32 / uint8 to float32 in [-1, 1]; pass float through.
    if data.dtype == np.int16:
        data = data.astype(np.float32) / 32768.0
    elif data.dtype == np.int32:
        data = data.astype(np.float32) / 2147483648.0
    elif data.dtype == np.uint8:
        data = (data.astype(np.float32) - 128.0) / 128.0
    else:
        data = data.astype(np.float32, copy=False)
    if data.ndim == 2:  # mix down stereo
        data = data.mean(axis=1)
    if sr != 16000:
        # Cheap linear resample; fine for probing.
        n = int(round(len(data) * 16000 / sr))
        data = np.interp(
            np.linspace(0, len(data) - 1, n), np.arange(len(data)), data
        ).astype(np.float32)
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--allowlist-only", action="store_true")
    parser.add_argument(
        "--model",
        type=Path,
        default=Path.home() / ".heare" / "models" / "yamnet.onnx",
    )
    args = parser.parse_args()

    classifier = YamnetClassifier(args.model)
    waveform = load_mono_16k(args.audio)
    win = YamnetClassifier.WINDOW_SAMPLES  # 15360 = 0.96 s
    hop = win // 2  # 0.48 s — matches observer's effective hop on a busy loop

    print(f"file:      {args.audio}")
    print(f"duration:  {len(waveform) / 16000:.2f} s")
    print(f"windows:   {1 + max(0, (len(waveform) - win) // hop)}\n")

    for i, start in enumerate(range(0, len(waveform) - win + 1, hop)):
        window = waveform[start : start + win]
        scores = classifier.classify(window)
        if args.allowlist_only:
            scores = [(idx, s) for idx, s in scores if label_for_index(idx)]
        print(f"-- t={start / 16000:5.2f}s -------------------------------")
        for idx, score in scores[: args.top_k]:
            tag = label_for_index(idx) or AUDIOSET_CLASSES[idx]
            print(f"  {score:.3f}  [{idx:3d}]  {tag}")


if __name__ == "__main__":
    main()
