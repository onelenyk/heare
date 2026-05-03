"""Daemon control helpers for the watch dashboard.

The watch dashboard normally only reads daemon state. These helpers let it
also drive the daemon lifecycle (start / stop / restart) via the existing
PID-file + SIGTERM contract used by ``heare start`` and ``heare stop``.

All public helpers return a short status string suitable for display in the
dashboard footer.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Settings


def daemon_pid(settings: "Settings") -> int | None:
    """Return the daemon's live PID, or None if not running.

    Cleans up nothing — read-only. Caller decides how to handle stale files.
    """
    pid_file: Path = settings.pid_file
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid
    return pid


def start_daemon(settings: "Settings") -> str:
    """Spawn the daemon detached. Output redirected to daemon.log."""
    if daemon_pid(settings) is not None:
        return "already running"

    log_dir = settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "daemon.log"

    project_dir = Path(__file__).resolve().parent.parent
    cmd = [sys.executable, "-m", "src.main", "start"]
    try:
        with open(log_path, "ab") as log_fh:
            subprocess.Popen(  # noqa: S603 — command is a fixed argv list
                cmd,
                cwd=str(project_dir),
                stdout=log_fh,
                stderr=log_fh,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
    except Exception as e:  # pragma: no cover — defensive
        return f"start failed: {e}"

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        pid = daemon_pid(settings)
        if pid is not None:
            return f"started (pid {pid})"
        time.sleep(0.1)
    return "started (pid not yet visible)"


def stop_daemon(settings: "Settings", *, timeout: float = 5.0) -> str:
    """Send SIGTERM and wait for exit; SIGKILL on timeout."""
    pid = daemon_pid(settings)
    if pid is None:
        # Drop a stale pid file if present so the next start works cleanly.
        try:
            settings.pid_file.unlink()
        except FileNotFoundError:
            pass
        return "not running"

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        try:
            settings.pid_file.unlink()
        except FileNotFoundError:
            pass
        return "not running"

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return f"stopped (pid {pid})"
        time.sleep(0.1)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return f"stopped (pid {pid})"
    return f"force-killed (pid {pid})"


def restart_daemon(settings: "Settings") -> str:
    """Stop then start. Returns a combined status message."""
    stop_msg = stop_daemon(settings)
    # Brief pause so the OS releases the PID and audio device cleanly.
    time.sleep(0.3)
    start_msg = start_daemon(settings)
    return f"{stop_msg} → {start_msg}"


__all__ = ["daemon_pid", "start_daemon", "stop_daemon", "restart_daemon"]
