"""Tests for src/speaker_id.py — mocks speechbrain so torch is not required."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np
import pytest


@pytest.fixture
def mock_speechbrain(monkeypatch):
    """Install a fake speechbrain + torch so speaker_id.load_model() works."""
    # Reset cached model
    import src.voice.speaker.id as sid_mod

    monkeypatch.setattr(sid_mod, "_model", None)

    class FakeTensor:
        def __init__(self, arr: np.ndarray) -> None:
            self.arr = arr

        def unsqueeze(self, dim: int) -> FakeTensor:  # noqa: F821
            return FakeTensor(self.arr[np.newaxis, :])

        def squeeze(self) -> FakeTensor:  # noqa: F821
            return FakeTensor(self.arr.squeeze())

        def cpu(self) -> FakeTensor:  # noqa: F821
            return self

        def numpy(self) -> np.ndarray:
            return self.arr

        def astype(self, dtype) -> FakeTensor:  # noqa: F821
            return FakeTensor(self.arr.astype(dtype))

    class FakeNoGrad:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    fake_torch = SimpleNamespace(
        from_numpy=lambda a: FakeTensor(a),
        no_grad=lambda: FakeNoGrad(),
    )

    class FakeClassifier:
        @classmethod
        def from_hparams(cls, **kwargs):  # noqa: ANN003
            return cls()

        def encode_batch(self, tensor: FakeTensor) -> FakeTensor:
            # Return a deterministic 192-dim vector derived from input length
            first_byte = int(tensor.arr[0][0] * 1000) & 0xFF
            vec = np.full(192, first_byte / 255.0, dtype=np.float32)
            return FakeTensor(vec[np.newaxis, :])

    fake_speaker_mod = SimpleNamespace(EncoderClassifier=FakeClassifier)
    fake_inference = SimpleNamespace(speaker=fake_speaker_mod)
    fake_speechbrain = SimpleNamespace(inference=fake_inference)

    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "speechbrain", fake_speechbrain)
    monkeypatch.setitem(sys.modules, "speechbrain.inference", fake_inference)
    monkeypatch.setitem(sys.modules, "speechbrain.inference.speaker", fake_speaker_mod)

    yield

    monkeypatch.setattr(sid_mod, "_model", None)


def test_load_model_caches(mock_speechbrain) -> None:
    import src.voice.speaker.id as sid

    m1 = sid.load_model()
    m2 = sid.load_model()
    assert m1 is m2


def test_warmup_runs_without_error(mock_speechbrain) -> None:
    import src.voice.speaker.id as sid

    model = sid.load_model()
    sid.warmup(model, sample_rate=16000)


def test_embed_returns_192_dim_vector(mock_speechbrain) -> None:
    import src.voice.speaker.id as sid

    model = sid.load_model()
    pcm = np.full(16000, 100, dtype=np.int16).tobytes()
    v = sid.embed(pcm, 16000, model)
    assert v.shape == (192,)
    assert v.dtype == np.float32
    # L2-normalized
    assert abs(np.linalg.norm(v) - 1.0) < 1e-5


def test_cosine_identical_vectors() -> None:
    import src.voice.speaker.id as sid

    v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    assert abs(sid.cosine(v, v) - 1.0) < 1e-6


def test_cosine_orthogonal_vectors() -> None:
    import src.voice.speaker.id as sid

    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    assert abs(sid.cosine(a, b)) < 1e-6


def test_cosine_opposite_vectors() -> None:
    import src.voice.speaker.id as sid

    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([-1.0, 0.0], dtype=np.float32)
    assert abs(sid.cosine(a, b) + 1.0) < 1e-6


def test_cosine_zero_vector_safe() -> None:
    import src.voice.speaker.id as sid

    a = np.array([0.0, 0.0], dtype=np.float32)
    b = np.array([1.0, 0.0], dtype=np.float32)
    # 1e-12 epsilon prevents division by zero
    result = sid.cosine(a, b)
    assert abs(result) < 1e-6
