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
    from src.watch_controls import daemon_pid

    assert daemon_pid(_settings(tmp_path)) is None


def test_daemon_pid_returns_none_for_dead_process(tmp_path: Path):
    from src.watch_controls import daemon_pid

    s = _settings(tmp_path)
    s.pid_file.write_text("999999999")  # almost certainly dead
    assert daemon_pid(s) is None


def test_daemon_pid_returns_pid_for_live_process(tmp_path: Path):
    from src.watch_controls import daemon_pid

    s = _settings(tmp_path)
    # Use this test process as the "live" daemon.
    s.pid_file.write_text(str(os.getpid()))
    assert daemon_pid(s) == os.getpid()


def test_daemon_pid_returns_none_for_garbage_pid_file(tmp_path: Path):
    from src.watch_controls import daemon_pid

    s = _settings(tmp_path)
    s.pid_file.write_text("not-a-number")
    assert daemon_pid(s) is None


def test_stop_daemon_when_not_running_cleans_stale_pid(tmp_path: Path):
    from src.watch_controls import stop_daemon

    s = _settings(tmp_path)
    s.pid_file.write_text("999999999")
    msg = stop_daemon(s)
    assert "not running" in msg
    assert not s.pid_file.exists()


def test_stop_daemon_when_no_pid_file(tmp_path: Path):
    from src.watch_controls import stop_daemon

    s = _settings(tmp_path)
    msg = stop_daemon(s)
    assert "not running" in msg


def test_stop_daemon_sigterm_pathway(tmp_path: Path, monkeypatch):
    """stop_daemon must SIGTERM the live pid and report success when the
    process exits within the timeout."""
    from src import watch_controls

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
    from src.watch_controls import start_daemon

    s = _settings(tmp_path)
    s.pid_file.write_text(str(os.getpid()))  # this test process IS the "daemon"
    msg = start_daemon(s)
    assert "already running" in msg


def test_start_daemon_spawns_subprocess(tmp_path: Path, monkeypatch):
    from src import watch_controls

    s = _settings(tmp_path)
    captured: dict = {}

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            # Simulate the daemon writing its pid file shortly after spawn.
            s.pid_file.parent.mkdir(parents=True, exist_ok=True)
            s.pid_file.write_text(str(os.getpid()))

    monkeypatch.setattr(watch_controls.subprocess, "Popen", FakePopen)

    msg = watch_controls.start_daemon(s)
    assert "started" in msg
    assert captured["cmd"][-2:] == ["src.main", "start"]
    assert captured["kwargs"].get("start_new_session") is True
    assert captured["kwargs"].get("stdin") is not None


def test_dispatch_key_routes_each_action(tmp_path: Path, monkeypatch):
    from src import watch

    s = _settings(tmp_path)
    s.mute_file = tmp_path / "mute.flag"

    monkeypatch.setattr(watch, "start_daemon", lambda s: "started")
    monkeypatch.setattr(watch, "stop_daemon", lambda s: "stopped")
    monkeypatch.setattr(watch, "restart_daemon", lambda s: "restarted")

    assert "started" in watch._dispatch_key(s, "s")
    assert "stopped" in watch._dispatch_key(s, "x")
    assert "restarted" in watch._dispatch_key(s, "r")
    # case-insensitive
    assert "started" in watch._dispatch_key(s, "S")
    # unknown key
    assert watch._dispatch_key(s, "z") is None


def test_dispatch_key_toggles_bot_mute(tmp_path: Path):
    from src import watch

    s = _settings(tmp_path)
    s.mute_file = tmp_path / "mute.flag"

    msg1 = watch._dispatch_key(s, "m")
    assert "bot muted" in msg1
    assert s.mute_file.exists()

    msg2 = watch._dispatch_key(s, "m")
    assert "bot unmuted" in msg2
    assert not s.mute_file.exists()


def test_dispatch_key_toggles_mic_mute(tmp_path: Path):
    from src import watch

    s = _settings(tmp_path)
    s.mute_file = tmp_path / "mute.flag"
    s.mute_input_file = tmp_path / "mute_input.flag"

    msg1 = watch._dispatch_key(s, "M")
    assert "mic muted" in msg1
    assert s.mute_input_file.exists()
    # bot mute untouched
    assert not s.mute_file.exists()

    msg2 = watch._dispatch_key(s, "M")
    assert "mic unmuted" in msg2
    assert not s.mute_input_file.exists()


def test_handle_input_key_appends_printable(tmp_path: Path):
    from src import watch

    s = _settings(tmp_path)
    s.inject_dir = tmp_path / "inject"

    buf, action = watch._handle_input_key(s, "hel", "l")
    assert buf == "hell"
    assert action is None


def test_handle_input_key_backspace(tmp_path: Path):
    from src import watch

    s = _settings(tmp_path)
    s.inject_dir = tmp_path / "inject"

    buf, _ = watch._handle_input_key(s, "abc", "\x7f")
    assert buf == "ab"
    buf, _ = watch._handle_input_key(s, "", "\x7f")
    assert buf == ""


def test_handle_input_key_escape_cancels(tmp_path: Path):
    from src import watch

    s = _settings(tmp_path)
    s.inject_dir = tmp_path / "inject"

    buf, action = watch._handle_input_key(s, "draft", "\x1b")
    assert buf is None
    assert "cancel" in action


def test_handle_input_key_enter_injects(tmp_path: Path):
    from src import watch

    s = _settings(tmp_path)
    s.inject_dir = tmp_path / "inject"

    buf, action = watch._handle_input_key(s, "play music", "\r")
    assert buf is None
    assert "injected" in action and "play music" in action
    queued = list(s.inject_dir.glob("*.txt"))
    assert len(queued) == 1
    assert queued[0].read_text() == "play music"


def test_handle_input_key_empty_enter_cancels(tmp_path: Path):
    from src import watch

    s = _settings(tmp_path)
    s.inject_dir = tmp_path / "inject"

    buf, action = watch._handle_input_key(s, "   ", "\n")
    assert buf is None
    assert "cancel" in action
    # nothing queued
    if s.inject_dir.exists():
        assert list(s.inject_dir.glob("*.txt")) == []


def test_restart_daemon_returns_combined_status(tmp_path: Path, monkeypatch):
    from src import watch_controls

    s = _settings(tmp_path)

    monkeypatch.setattr(watch_controls, "stop_daemon", lambda s: "not running")
    monkeypatch.setattr(watch_controls, "start_daemon", lambda s: "started (pid 42)")
    # Skip the sleep so the test stays fast.
    monkeypatch.setattr(watch_controls.time, "sleep", lambda *_: None)

    msg = watch_controls.restart_daemon(s)
    assert "not running" in msg and "started" in msg
