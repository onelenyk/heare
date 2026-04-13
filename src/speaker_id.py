"""ECAPA-TDNN speaker embedding wrapper.

speechbrain + torch are imported lazily inside load_model() so the heare
daemon can start and tests can run without the 200MB torch wheel when
speaker_id_enabled=False.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import numpy as np


logger = logging.getLogger("heare.speaker_id")

_model: Any = None


def load_model() -> Any:
    global _model
    if _model is not None:
        return _model
    from speechbrain.inference.speaker import EncoderClassifier  # type: ignore

    _model = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir=str(Path.home() / ".heare" / "speaker_model"),
        run_opts={"device": "cpu"},
    )
    return _model


def warmup(model: Any, sample_rate: int = 16000) -> None:
    """Force JIT + BLAS kernel compilation with one-second white-noise PCM.

    Silent PCM can short-circuit some BLAS fast-paths; white noise exercises
    the same code path as real speech without waiting for a user utterance.
    """
    t0 = time.monotonic()
    noise = np.random.randint(-1000, 1000, sample_rate, dtype=np.int16)
    pcm = noise.tobytes()
    embed(pcm, sample_rate, model)
    elapsed_ms = (time.monotonic() - t0) * 1000.0
    logger.info("[SPEAKER] warmup complete (%.0fms)", elapsed_ms)


def embed(pcm: bytes, sample_rate: int, model: Any) -> np.ndarray:
    """Compute an L2-normalized speaker embedding from int16 PCM bytes."""
    import torch  # type: ignore

    arr = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    tensor = torch.from_numpy(arr).unsqueeze(0)  # shape (1, samples)
    with torch.no_grad():
        emb = model.encode_batch(tensor)
    vec = emb.squeeze().cpu().numpy().astype(np.float32)
    norm = np.linalg.norm(vec) + 1e-12
    return vec / norm


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(
        np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12)
    )
