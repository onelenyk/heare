"""Tests for src/voice/speaker/id.py — onnxruntime path, no torch/onnx pkg.

The ORT InferenceSession is faked via sys.modules so these run without a
real model file or the onnx/onnxruntime wheels.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np
import pytest


class _FakeInput:
    def __init__(self, name: str, shape: list) -> None:
        self.name = name
        self.shape = shape


class _FakeSession:
    """Echoes a deterministic 192-dim vector; records the last feed."""

    def __init__(self, shape: list) -> None:
        self._shape = shape
        self.last_feed: dict | None = None

    def get_inputs(self):
        return [_FakeInput("feats", self._shape)]

    def run(self, _outputs, feed):
        self.last_feed = feed
        x = next(iter(feed.values()))
        seed = float(np.abs(x).mean())
        return [np.full((1, 192), seed + 0.1, dtype=np.float32)]


@pytest.fixture
def fake_ort(monkeypatch):
    """Install a fake onnxruntime and reset the module-level model cache."""
    import src.voice.speaker.id as sid

    monkeypatch.setattr(sid, "_model", None)

    holder: dict = {"shape": [1, "T", 80]}  # BTC by default

    class FakeSessionOptions:
        intra_op_num_threads = 1
        inter_op_num_threads = 1

    def make_session(path, sess_options=None, providers=None):
        return _FakeSession(holder["shape"])

    fake_ort_mod = SimpleNamespace(
        SessionOptions=FakeSessionOptions,
        InferenceSession=make_session,
    )
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort_mod)
    yield holder
    monkeypatch.setattr(sid, "_model", None)


@pytest.fixture
def model_file(tmp_path):
    p = tmp_path / "speaker.onnx"
    p.write_bytes(b"\x00")  # content irrelevant; ORT is faked
    return p


def test_load_model_caches(fake_ort, model_file) -> None:
    import src.voice.speaker.id as sid

    m1 = sid.load_model(model_path=model_file)
    m2 = sid.load_model(model_path=model_file)
    assert m1 is m2
    assert m1.input_name == "feats"
    assert m1.layout == "BTC"


def test_missing_model_raises(fake_ort, tmp_path) -> None:
    import src.voice.speaker.id as sid

    with pytest.raises(FileNotFoundError):
        sid.load_model(model_path=tmp_path / "nope.onnx")


def test_warmup_runs_without_error(fake_ort, model_file) -> None:
    import src.voice.speaker.id as sid

    sid.warmup(sid.load_model(model_path=model_file), sample_rate=16000)


def test_embed_returns_normalized_192(fake_ort, model_file) -> None:
    import src.voice.speaker.id as sid

    model = sid.load_model(model_path=model_file)
    pcm = (np.random.randn(16000) * 3000).astype(np.int16).tobytes()
    v = sid.embed(pcm, 16000, model)
    assert v.shape == (192,)
    assert v.dtype == np.float32
    assert abs(np.linalg.norm(v) - 1.0) < 1e-5


def test_embed_rejects_wrong_sample_rate(fake_ort, model_file) -> None:
    import src.voice.speaker.id as sid

    model = sid.load_model(model_path=model_file)
    with pytest.raises(ValueError):
        sid.embed(np.zeros(8000, dtype=np.int16).tobytes(), 8000, model)


def test_layout_bct_feeds_channel_first(fake_ort, model_file) -> None:
    import src.voice.speaker.id as sid

    fake_ort["shape"] = [1, 80, "T"]  # BCT
    model = sid.load_model(model_path=model_file)
    assert model.layout == "BCT"
    sid.embed(np.zeros(16000, dtype=np.int16).tobytes(), 16000, model)
    fed = next(iter(model.session.last_feed.values()))
    assert fed.shape[1] == 80  # channel-first (1, 80, frames)


def test_layout_btc_feeds_time_first(fake_ort, model_file) -> None:
    import src.voice.speaker.id as sid

    model = sid.load_model(model_path=model_file)
    assert model.layout == "BTC"
    sid.embed(np.zeros(16000, dtype=np.int16).tobytes(), 16000, model)
    fed = next(iter(model.session.last_feed.values()))
    assert fed.shape[2] == 80  # time-first (1, frames, 80)


def test_fbank_shape_and_cmn() -> None:
    import src.voice.speaker.id as sid

    samples = (np.random.randn(16000) * 0.1).astype(np.float32)
    feats = sid._log_mel_fbank(samples, 16000)
    assert feats.shape[1] == 80
    assert feats.shape[0] == 1 + (16000 - 400) // 160
    # per-utterance CMN -> each mel bin ~ zero mean over time
    assert np.allclose(feats.mean(axis=0), 0.0, atol=1e-4)


def test_fbank_pads_short_input() -> None:
    import src.voice.speaker.id as sid

    feats = sid._log_mel_fbank(np.zeros(100, dtype=np.float32), 16000)
    assert feats.shape == (1, 80)


def test_cosine_identical_orthogonal_opposite_zero() -> None:
    import src.voice.speaker.id as sid

    v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    assert abs(sid.cosine(v, v) - 1.0) < 1e-6
    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    assert abs(sid.cosine(a, b)) < 1e-6
    assert abs(sid.cosine(np.array([1.0, 0.0]), np.array([-1.0, 0.0])) + 1.0) < 1e-6
    assert abs(sid.cosine(np.array([0.0, 0.0]), np.array([1.0, 0.0]))) < 1e-6
