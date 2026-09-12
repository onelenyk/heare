"""Tests for src/main.py CLI commands."""
from __future__ import annotations

import asyncio
import fcntl
import os
from unittest.mock import Mock


def test_multiple_daemon_instances_prevented(tmp_path, monkeypatch) -> None:
    """Starting a second daemon instance fails gracefully when first is running.

    ``_cmd_start``'s single-instance guard is an ``flock`` on the pid file,
    not a check of the pid file's contents (see src/main.py, the "File
    lock" block). Hold that same lock here, the way a real running daemon
    would, instead of relying on the machine already having something
    bound to the hardcoded daemon port — that made this test pass or fail
    depending on whether a live `heare` daemon happened to be running on
    9780.
    """
    monkeypatch.setattr("src.config.HEARE_HOME", tmp_path)

    pid_file = tmp_path / "heare.pid"
    pid_file.write_text(str(os.getpid()))

    lock_fd = os.open(pid_file, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        from src.main import _cmd_start

        args = Mock()
        result = asyncio.run(_cmd_start(args))

        assert result == 1
        assert pid_file.exists()
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
