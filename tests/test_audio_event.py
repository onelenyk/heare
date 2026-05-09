"""Tests for the YAMNet audio event subsystem.

Every test except ``test_smoke_classifier`` runs without the ONNX model
file present. The smoke test is gated on ``HEARE_TEST_YAMNET=1`` so CI
can stay green on machines that have not run the tf2onnx conversion.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

from src.audio_event.class_map import (
    ALLOWLIST,
    AUDIOSET_CLASSES,
    label_for_index,
)
from src.audio_event.observer import (
    AudioEventObserver,
    create_audio_event_observer,
)
from src.audio_event.writer import write_audio_event
from src.config import Settings
from src.watch.data import AudioEventData, read_audio_event


# ---------------------------------------------------------------------------
# class_map
# ---------------------------------------------------------------------------


def test_class_map_length():
    assert len(AUDIOSET_CLASSES) == 521


def test_allowlist_is_hardcoded_dict():
    assert isinstance(ALLOWLIST, dict)
    for idx, label in ALLOWLIST.items():
        assert isinstance(idx, int) and isinstance(label, str)


def test_label_for_index_speech_excluded():
    assert label_for_index(0) is None  # AudioSet 0 = "Speech"


def test_label_for_index_laughter():
    # Verified against the canonical YAMNet class map (idx 13 = Laughter).
    assert label_for_index(13) == "Laughter"


def test_allowlist_covers_curated_set():
    expected = {
        # Human non-speech
        "Laughter", "Giggle", "Cough", "Sneeze", "Screaming", "Crying",
        "Whispering",
        # Pets
        "Bark", "Howl", "Bow-wow", "Whimper", "Meow", "Purr",
        "Hiss", "Caterwaul", "Bird vocalization", "Crowing",
        # Home awareness
        "Music", "Knock", "Phone ringing", "Ringtone", "Alarm clock",
        "TV", "Radio",
    }
    assert expected.issubset(set(ALLOWLIST.values()))


# ---------------------------------------------------------------------------
# writer
# ---------------------------------------------------------------------------


def test_write_audio_event_atomic(tmp_path):
    p = tmp_path / "audio_event.json"
    write_audio_event(p, "Laughter", 0.7123)
    payload = json.loads(p.read_text())
    assert set(payload.keys()) == {"label", "score", "ts"}
    assert payload["label"] == "Laughter"
    assert payload["score"] == 0.712  # rounded to 3 decimals
    assert payload["ts"] > 0


def test_write_audio_event_creates_parent_dir(tmp_path):
    p = tmp_path / "nested" / "subdir" / "audio_event.json"
    write_audio_event(p, "Bark", 0.5)
    assert p.exists()


def test_write_audio_event_swallows_oserror(tmp_path):
    # Writing to a path inside a nonexistent read-only mount would raise.
    # Easier: mock os.replace to throw.
    p = tmp_path / "audio_event.json"
    with mock.patch("src.audio_event.writer.os.replace", side_effect=OSError("boom")):
        # Must not raise.
        write_audio_event(p, "Bark", 0.5)


# ---------------------------------------------------------------------------
# read_audio_event (dashboard side)
# ---------------------------------------------------------------------------


def test_read_audio_event_missing_file(tmp_path):
    p = tmp_path / "does_not_exist.json"
    data = read_audio_event(p)
    assert data == AudioEventData(label=None, score=0.0, ts=0.0)


def test_read_audio_event_corrupt(tmp_path):
    p = tmp_path / "audio_event.json"
    p.write_text("{ not valid json")
    data = read_audio_event(p)
    assert data == AudioEventData(label=None, score=0.0, ts=0.0)


# ---------------------------------------------------------------------------
# observer state machine
# ---------------------------------------------------------------------------


class _StubFrame:
    """Non-InputAudioRawFrame frame used to verify pass-through."""


def _make_observer(tmp_path, classifier=None, threshold=0.4):
    obs = AudioEventObserver(
        classifier=classifier,
        state_file=tmp_path / "audio_event.json",
        threshold=threshold,
    )
    # Capture pushed frames + bypass FrameProcessor's start-frame requirement.
    obs._FrameProcessor__started = True  # type: ignore[attr-defined]
    pushed: list = []

    async def _push(frame, direction):
        pushed.append(frame)

    obs.push_frame = _push  # type: ignore[assignment]
    return obs, pushed


def test_observer_passthrough(tmp_path):
    obs, pushed = _make_observer(tmp_path)
    frame = _StubFrame()
    asyncio.run(obs.process_frame(frame, mock.MagicMock()))
    assert pushed == [frame]


def test_observer_drops_when_busy(tmp_path):
    """When _running=True, a fully-buffered window is discarded."""
    from pipecat.frames.frames import InputAudioRawFrame

    obs, _ = _make_observer(tmp_path)
    obs._running = True
    obs._buf = bytearray(obs.WINDOW_BYTES)
    pcm = bytes(obs.WINDOW_BYTES)
    frame = InputAudioRawFrame(audio=pcm, sample_rate=16000, num_channels=1)
    asyncio.run(obs.process_frame(frame, mock.MagicMock()))
    # Buffer drained (drop policy), inference NOT scheduled.
    assert len(obs._buf) <= obs.WINDOW_BYTES


def test_observer_two_window_confirmation(tmp_path):
    """Single window match emits no event; two consecutive matches do."""
    obs, _ = _make_observer(tmp_path)
    state_file = obs._state_file

    # Window 1: Laughter at 0.7 — set _prev_label to None first.
    obs._on_result(_FakeFuture(("Laughter", 0.7)))
    assert obs._prev_label == "Laughter"
    assert not state_file.exists()  # No event yet — first window.

    # Window 2: Laughter again — confirm and emit.
    obs._on_result(_FakeFuture(("Laughter", 0.72)))
    assert state_file.exists()
    payload = json.loads(state_file.read_text())
    assert payload["label"] == "Laughter"


def test_observer_label_change_resets(tmp_path):
    """Different label on window 2 resets — no event until 3rd window matches."""
    obs, _ = _make_observer(tmp_path)
    state_file = obs._state_file

    obs._on_result(_FakeFuture(("Laughter", 0.7)))
    obs._on_result(_FakeFuture(("Cough", 0.6)))
    assert not state_file.exists()
    obs._on_result(_FakeFuture(("Cough", 0.65)))
    assert state_file.exists()
    payload = json.loads(state_file.read_text())
    assert payload["label"] == "Cough"


def test_observer_below_threshold(tmp_path):
    """Sub-threshold predictions do not trigger emission OR set _prev_label."""
    obs, _ = _make_observer(tmp_path, threshold=0.4)
    state_file = obs._state_file

    # Two windows of None (sub-threshold from _infer's perspective).
    obs._on_result(_FakeFuture(None))
    obs._on_result(_FakeFuture(None))
    assert not state_file.exists()
    assert obs._prev_label is None


def test_observer_running_resets_on_infer_exception(tmp_path):
    """An exception inside _infer must not deadlock the observer (_running -> False)."""
    obs, _ = _make_observer(tmp_path)
    obs._running = True
    fut = _FakeFuture(None, exc=RuntimeError("boom"))
    obs._on_result(fut)
    assert obs._running is False
    assert obs._prev_label is None


class _FakeFuture:
    """Stand-in for ``asyncio.Future`` accepted by ``_on_result``."""

    def __init__(self, value, exc=None):
        self._value = value
        self._exc = exc

    def result(self):
        if self._exc is not None:
            raise self._exc
        return self._value


# ---------------------------------------------------------------------------
# factory
# ---------------------------------------------------------------------------


def test_factory_disabled(tmp_path):
    settings = Settings()
    settings.audio_event_detection_enabled = False
    assert create_audio_event_observer(settings) is None


def test_factory_missing_model(tmp_path, monkeypatch, caplog):
    settings = Settings()
    settings.audio_event_detection_enabled = True
    settings.yamnet_model_path = tmp_path / "does_not_exist.onnx"
    with caplog.at_level("WARNING"):
        assert create_audio_event_observer(settings) is None
    assert any("model not found" in r.message for r in caplog.records)


def test_factory_missing_onnxruntime(tmp_path, monkeypatch, caplog):
    settings = Settings()
    settings.audio_event_detection_enabled = True
    # Simulate missing onnxruntime via import error inside the factory.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "onnxruntime":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with caplog.at_level("WARNING"):
        assert create_audio_event_observer(settings) is None
    assert any("onnxruntime not installed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# pipeline integration
# ---------------------------------------------------------------------------


def test_build_pipeline_does_not_import_when_disabled():
    """When the flag is off, ``src.audio_event.observer`` must not be imported.

    We can not actually call ``build_pipeline`` here without a real
    transport, but we can prove the import contract: importing
    ``src.pipeline.build`` itself does not eagerly load the observer
    module. The conditional import lives inside ``build_pipeline``.
    """
    # Force a clean re-import of build to confirm no top-level audio_event
    # import is triggered.
    for name in list(sys.modules):
        if name == "src.audio_event.observer":
            del sys.modules[name]
    import src.pipeline.build  # noqa: F401
    assert "src.audio_event.observer" not in sys.modules


# ---------------------------------------------------------------------------
# DashboardSnapshot integration
# ---------------------------------------------------------------------------


def test_dashboard_snapshot_includes_audio_event(tmp_path, monkeypatch):
    """``fetch_dashboard_state`` populates ``snapshot.audio_event``."""
    from src.watch import data as watch_data

    settings = Settings()
    settings.db_path = tmp_path / "heare.db"
    settings.log_dir = tmp_path / "logs"
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    settings.pid_file = tmp_path / "heare.pid"  # absent → daemon "stopped"
    settings.mute_file = tmp_path / "mute.flag"
    settings.mute_input_file = tmp_path / "mute_input.flag"
    settings.voice_state_file = tmp_path / "voice_state.json"
    settings.audio_event_file = tmp_path / "audio_event.json"
    settings.identity_file = tmp_path / "identity.json"
    settings.speakers_file = tmp_path / "speakers.json"
    settings.provider_file = tmp_path / "provider"

    # Pre-write an event so the reader has something to load.
    write_audio_event(settings.audio_event_file, "Bark", 0.55)

    snapshot = watch_data.fetch_dashboard_state(settings)
    assert snapshot.audio_event.label == "Bark"
    assert snapshot.audio_event.score == 0.55


# ---------------------------------------------------------------------------
# Settings defaults
# ---------------------------------------------------------------------------


def test_settings_defaults():
    s = Settings()
    assert s.audio_event_detection_enabled is False
    assert s.audio_event_threshold == 0.4
    assert s.yamnet_model_path.name == "yamnet.onnx"
    assert s.audio_event_file.name == "audio_event.json"


# ---------------------------------------------------------------------------
# Smoke test — gated on HEARE_TEST_YAMNET=1
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("HEARE_TEST_YAMNET") != "1",
    reason="set HEARE_TEST_YAMNET=1 to run the real-model smoke test",
)
def test_smoke_classifier():
    """Loads the real model and confirms classify() returns 521 predictions."""
    import numpy as np

    from src.audio_event.classifier import YamnetClassifier

    model_path = Path(
        os.environ.get(
            "HEARE_YAMNET_MODEL",
            str(Path.home() / ".heare" / "models" / "yamnet.onnx"),
        )
    )
    classifier = YamnetClassifier(model_path)
    waveform = np.zeros(YamnetClassifier.WINDOW_SAMPLES, dtype=np.float32)
    scores = classifier.classify(waveform)
    assert len(scores) == 521
