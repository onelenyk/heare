"""ECAPA-TDNN speaker embedding via onnxruntime (no torch/speechbrain).

The embedding model is a pre-exported ECAPA/CAM++-style ONNX graph that
consumes 80-dim log-mel Fbank features (16 kHz, 25 ms window, 10 ms hop,
per-utterance cepstral mean normalization) — the de-facto wespeaker /
3D-Speaker input contract. The Fbank frontend is computed here in numpy so
the only runtime dependency is onnxruntime (already required for
audio-event detection).

onnxruntime is imported lazily inside load_model() so the heare daemon can
start and tests can run when speaker_id_enabled=False.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


logger = logging.getLogger("heare.speaker_id")

# Standard ECAPA / wespeaker Fbank contract.
_SAMPLE_RATE = 16000
_N_MELS = 80
_FRAME_LEN = 400  # 25 ms @ 16 kHz
_FRAME_SHIFT = 160  # 10 ms @ 16 kHz
_N_FFT = 512  # next pow2 >= frame length
_PREEMPH = 0.97
_EPS = 1e-12

_model: Any = None


def default_model_path() -> Path:
    return Path.home() / ".heare" / "speaker_model" / "speaker.onnx"


@dataclass
class SpeakerModel:
    """Opaque handle passed back into embed() — holds the ORT session and the
    detected input layout so embed() does not re-introspect on every call."""

    session: Any
    input_name: str
    # "BTC" -> (1, frames, n_mels); "BCT" -> (1, n_mels, frames)
    layout: str


def _resolve_model_path() -> Path:
    try:
        from src.config import get_settings  # local import; avoids cycle at module load

        settings = get_settings()
        p = getattr(settings, "speaker_id_onnx_path", None)
        if p:
            return Path(p).expanduser()
    except Exception:  # config not available (e.g. isolated unit test)
        pass
    return default_model_path()


def load_model(model_path: str | Path | None = None) -> SpeakerModel:
    global _model
    if _model is not None:
        return _model

    import onnxruntime as ort  # lazy: keeps daemon/tests torch- and ort-free until needed

    path = Path(model_path).expanduser() if model_path else _resolve_model_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"Speaker ONNX model not found at {path}. "
            f"Run `python -m scripts.fetch_speaker_onnx` to download it, "
            f"or set speaker_id_onnx_path in config."
        )

    so = ort.SessionOptions()
    so.intra_op_num_threads = 1
    so.inter_op_num_threads = 1
    session = ort.InferenceSession(
        str(path), sess_options=so, providers=["CPUExecutionProvider"]
    )

    inp = session.get_inputs()[0]
    # Shape is typically [batch, frames, 80] or [batch, 80, frames] with
    # symbolic dims (str/None) for the dynamic axes. Pick layout by which
    # static dim equals the mel count.
    shape = inp.shape
    layout = "BTC"
    if len(shape) == 3 and shape[1] == _N_MELS and shape[2] != _N_MELS:
        layout = "BCT"
    _model = SpeakerModel(session=session, input_name=inp.name, layout=layout)
    logger.info(
        "[SPEAKER] onnx model loaded path=%s input=%s shape=%s layout=%s",
        path,
        inp.name,
        shape,
        layout,
    )
    return _model


def warmup(model: SpeakerModel, sample_rate: int = _SAMPLE_RATE) -> None:
    """Force ORT graph allocation with one second of white-noise PCM.

    Silent PCM can short-circuit some kernel fast-paths; white noise
    exercises the same code path as real speech without waiting for a user
    utterance.
    """
    t0 = time.monotonic()
    noise = np.random.randint(-1000, 1000, sample_rate, dtype=np.int16)
    embed(noise.tobytes(), sample_rate, model)
    logger.info("[SPEAKER] warmup complete (%.0fms)", (time.monotonic() - t0) * 1000.0)


def _log_mel_fbank(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """80-dim log-mel Fbank, frame-major (frames, 80), with per-utterance CMN.

    Deterministic numpy implementation of the standard 16 kHz / 25 ms / 10 ms
    Fbank used by wespeaker and 3D-Speaker ECAPA exports.
    """
    if sample_rate != _SAMPLE_RATE:
        raise ValueError(
            f"speaker embed expects {_SAMPLE_RATE} Hz PCM, got {sample_rate}"
        )
    if samples.size < _FRAME_LEN:
        samples = np.pad(samples, (0, _FRAME_LEN - samples.size))

    n_frames = 1 + (samples.size - _FRAME_LEN) // _FRAME_SHIFT
    idx = np.arange(_FRAME_LEN)[None, :] + _FRAME_SHIFT * np.arange(n_frames)[:, None]
    frames = samples[idx].astype(np.float64)

    # Per-frame DC removal + pre-emphasis + Povey window (Kaldi default).
    frames -= frames.mean(axis=1, keepdims=True)
    frames[:, 1:] -= _PREEMPH * frames[:, :-1]
    frames[:, 0] -= _PREEMPH * frames[:, 0]
    n = np.arange(_FRAME_LEN)
    povey = (0.5 - 0.5 * np.cos(2.0 * np.pi * n / (_FRAME_LEN - 1))) ** 0.85
    frames *= povey

    power = np.abs(np.fft.rfft(frames, n=_N_FFT, axis=1)) ** 2
    fb = _mel_filterbank(sample_rate)
    mel = np.maximum(power @ fb.T, _EPS)
    log_mel = np.log(mel)

    # Per-utterance cepstral mean normalization (wespeaker default).
    log_mel -= log_mel.mean(axis=0, keepdims=True)
    return log_mel.astype(np.float32)


_FB_CACHE: dict[int, np.ndarray] = {}


def _mel_filterbank(sample_rate: int) -> np.ndarray:
    fb = _FB_CACHE.get(sample_rate)
    if fb is not None:
        return fb

    def hz_to_mel(f: np.ndarray | float) -> np.ndarray | float:
        return 1127.0 * np.log(1.0 + np.asarray(f) / 700.0)

    def mel_to_hz(m: np.ndarray) -> np.ndarray:
        return 700.0 * (np.exp(m / 1127.0) - 1.0)

    low, high = 20.0, sample_rate / 2.0
    mel_pts = np.linspace(hz_to_mel(low), hz_to_mel(high), _N_MELS + 2)
    hz_pts = mel_to_hz(mel_pts)
    bins = np.floor((_N_FFT + 1) * hz_pts / sample_rate).astype(int)

    fb = np.zeros((_N_MELS, _N_FFT // 2 + 1), dtype=np.float64)
    for m in range(1, _N_MELS + 1):
        lo, c, hi = bins[m - 1], bins[m], bins[m + 1]
        for k in range(lo, c):
            if c > lo:
                fb[m - 1, k] = (k - lo) / (c - lo)
        for k in range(c, hi):
            if hi > c:
                fb[m - 1, k] = (hi - k) / (hi - c)
    _FB_CACHE[sample_rate] = fb
    return fb


def embed(pcm: bytes, sample_rate: int, model: SpeakerModel) -> np.ndarray:
    """Compute an L2-normalized speaker embedding from int16 PCM bytes."""
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    feats = _log_mel_fbank(samples, sample_rate)  # (frames, 80)

    if model.layout == "BCT":
        x = feats.T[None, :, :]  # (1, 80, frames)
    else:
        x = feats[None, :, :]  # (1, frames, 80)

    out = model.session.run(None, {model.input_name: x.astype(np.float32)})[0]
    vec = np.asarray(out).reshape(-1).astype(np.float32)
    return vec / (np.linalg.norm(vec) + _EPS)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(
        np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + _EPS)
    )
