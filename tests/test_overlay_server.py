"""Tests for src/overlay/server.py — endpoint contract + flag wiring."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

# Skip the whole module if FastAPI isn't installed — overlay deps are optional.
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402


@dataclass
class _StubSettings:
    """Minimal settings double — only the fields the overlay server reads."""

    mute_file: Path
    mute_input_file: Path
    cancel_flag_file: Path
    voice_state_file: Path
    audio_event_file: Path


def _make_settings(tmp_path: Path) -> _StubSettings:
    return _StubSettings(
        mute_file=tmp_path / "mute.flag",
        mute_input_file=tmp_path / "mute_input.flag",
        cancel_flag_file=tmp_path / "cancel.flag",
        voice_state_file=tmp_path / "voice_state.json",
        audio_event_file=tmp_path / "audio_event.json",
    )


def _client(tmp_path: Path) -> tuple[TestClient, _StubSettings]:
    from src.overlay.server import create_app

    settings = _make_settings(tmp_path)
    return TestClient(create_app(settings)), settings  # type: ignore[arg-type]


def test_state_when_no_files_returns_safe_defaults(tmp_path: Path):
    client, _ = _client(tmp_path)
    r = client.get("/api/state")
    assert r.status_code == 200
    body = r.json()
    assert body["voice_state"] is None
    assert body["audio_event"] is None
    assert body["mute_output"] is False
    assert body["mute_input"] is False
    assert body["daemon_online"] is False


def test_state_reads_voice_state_and_audio_event(tmp_path: Path):
    client, settings = _client(tmp_path)
    settings.voice_state_file.write_text(
        json.dumps(
            {
                "state": "listening",
                "since_ts": time.time(),
                "last_partial": "what",
                "last_final": None,
            }
        )
    )
    settings.audio_event_file.write_text(
        json.dumps({"label": "Speech", "score": 0.81, "ts": time.time()})
    )
    r = client.get("/api/state")
    body = r.json()
    assert body["voice_state"]["state"] == "listening"
    assert body["audio_event"]["label"] == "Speech"
    assert body["daemon_online"] is True


def test_state_daemon_offline_when_voice_state_stale(tmp_path: Path):
    client, settings = _client(tmp_path)
    # Write the file with an mtime far in the past (well outside the
    # DAEMON_FRESHNESS_SECONDS window).
    settings.voice_state_file.write_text(json.dumps({"state": "idle"}))
    old = time.time() - 600
    import os

    os.utime(settings.voice_state_file, (old, old))
    body = client.get("/api/state").json()
    assert body["daemon_online"] is False


def test_mute_output_toggles_flag(tmp_path: Path):
    client, settings = _client(tmp_path)
    assert not settings.mute_file.exists()
    r = client.post("/api/mute/output", json={"on": True})
    assert r.json() == {"muted": True}
    assert settings.mute_file.exists()
    r = client.post("/api/mute/output", json={"on": False})
    assert r.json() == {"muted": False}
    assert not settings.mute_file.exists()


def test_mute_input_toggles_flag(tmp_path: Path):
    client, settings = _client(tmp_path)
    r = client.post("/api/mute/input", json={"on": True})
    assert r.json() == {"muted": True}
    assert settings.mute_input_file.exists()
    # Output flag must not be touched.
    assert not settings.mute_file.exists()


def test_cancel_creates_cancel_flag(tmp_path: Path):
    client, settings = _client(tmp_path)
    assert not settings.cancel_flag_file.exists()
    r = client.post("/api/cancel")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert settings.cancel_flag_file.exists()


def test_bind_host_is_loopback():
    from src.overlay.server import bind_host

    assert bind_host() == "127.0.0.1"


def test_index_route_serves_html(tmp_path: Path):
    client, _ = _client(tmp_path)
    r = client.get("/")
    # Static dir is bundled with the package — the route must exist and
    # return the HTML.
    assert r.status_code == 200
    assert "<title>heare</title>" in r.text
