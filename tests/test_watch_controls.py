"""Tests for src/watch_controls.py — daemon lifecycle helpers used by the
interactive watch dashboard."""
from __future__ import annotations

import os
import signal
from pathlib import Path



def _settings(tmp: Path):
    """Build a minimal Settings stand-in. We only need pid_file + log_dir
    on the object, so a SimpleNamespace works."""
    from types import SimpleNamespace

    return SimpleNamespace(
        pid_file=tmp / "heare.pid",
        log_dir=tmp / "logs",
    )


def test_daemon_pid_returns_none_when_no_pid_file(tmp_path: Path):
    from src.daemon.watch_controls import daemon_pid

    assert daemon_pid(_settings(tmp_path)) is None


def test_daemon_pid_returns_none_for_dead_process(tmp_path: Path):
    from src.daemon.watch_controls import daemon_pid

    s = _settings(tmp_path)
    s.pid_file.write_text("999999999")  # almost certainly dead
    assert daemon_pid(s) is None


def test_daemon_pid_returns_pid_for_live_process(tmp_path: Path):
    from src.daemon.watch_controls import daemon_pid

    s = _settings(tmp_path)
    # Use this test process as the "live" daemon.
    s.pid_file.write_text(str(os.getpid()))
    assert daemon_pid(s) == os.getpid()


def test_daemon_pid_returns_none_for_garbage_pid_file(tmp_path: Path):
    from src.daemon.watch_controls import daemon_pid

    s = _settings(tmp_path)
    s.pid_file.write_text("not-a-number")
    assert daemon_pid(s) is None


def test_stop_daemon_when_not_running_cleans_stale_pid(tmp_path: Path):
    from src.daemon.watch_controls import stop_daemon

    s = _settings(tmp_path)
    s.pid_file.write_text("999999999")
    msg = stop_daemon(s)
    assert "not running" in msg
    assert not s.pid_file.exists()


def test_stop_daemon_when_no_pid_file(tmp_path: Path):
    from src.daemon.watch_controls import stop_daemon

    s = _settings(tmp_path)
    msg = stop_daemon(s)
    assert "not running" in msg


def test_stop_daemon_sigterm_pathway(tmp_path: Path, monkeypatch):
    """stop_daemon must SIGTERM the live pid and report success when the
    process exits within the timeout."""
    from src.daemon import watch_controls

    s = _settings(tmp_path)
    s.pid_file.write_text("12345")

    sent: list[tuple[int, int]] = []
    alive = {"v": True}

    def fake_kill(pid: int, sig: int) -> None:
        sent.append((pid, sig))
        if sig == 0 and not alive["v"]:
            raise ProcessLookupError
        if sig == signal.SIGTERM:
            alive["v"] = False  # process "exits" after SIGTERM

    monkeypatch.setattr(watch_controls.os, "kill", fake_kill)
    msg = watch_controls.stop_daemon(s, timeout=1.0)

    assert "stopped" in msg
    assert (12345, signal.SIGTERM) in sent


def test_start_daemon_refuses_when_already_running(tmp_path: Path):
    from src.daemon.watch_controls import start_daemon

    s = _settings(tmp_path)
    s.pid_file.write_text(str(os.getpid()))  # this test process IS the "daemon"
    msg = start_daemon(s)
    assert "already running" in msg


def test_start_daemon_spawns_subprocess(tmp_path: Path, monkeypatch):
    from src.daemon import watch_controls

