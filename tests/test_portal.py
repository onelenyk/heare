"""Tests for _ensure_portal() portal startup logic."""
from __future__ import annotations

import asyncio
import socket
import subprocess
import sys


def test_ensure_portal_returns_true_when_port_open(monkeypatch) -> None:
    """_ensure_portal returns True without spawning when port 9780 is already open."""
    from src.main import _ensure_portal

    def _mock_create_connection(address, timeout=0.5):
        return socket.socket()

    monkeypatch.setattr(socket, "create_connection", _mock_create_connection)

    result = asyncio.run(_ensure_portal(timeout=0.2))
    assert result is True


def test_ensure_portal_unfrozen_path(monkeypatch) -> None:
    """_ensure_portal works when not frozen — spawns correct cmd, returns bool."""
    frozen = getattr(sys, "frozen", None)
    try:
        if hasattr(sys, "frozen"):
            del sys.frozen

        def _mock_create_connection(address, timeout=0.5):
            raise OSError("Connection refused")

        monkeypatch.setattr(socket, "create_connection", _mock_create_connection)

        popen_calls = []
        class _FakePopen:
            def __init__(self, cmd, **kwargs):
                popen_calls.append(cmd)

        monkeypatch.setattr(subprocess, "Popen", _FakePopen)

        from src.main import _ensure_portal

        result = asyncio.run(_ensure_portal(timeout=0.1))
        assert result is False
        assert len(popen_calls) == 1
        assert popen_calls[0] == [sys.executable, "-m", "src.main", "portal"]
    finally:
        if frozen is not None:
            sys.frozen = frozen


def test_ensure_portal_handles_socket_errors_gracefully(monkeypatch) -> None:
    """_ensure_portal returns False when port is closed and subprocess fails."""
    from src.main import _ensure_portal

    def _mock_create_connection(address, timeout=0.5):
        raise OSError("Connection refused")

    monkeypatch.setattr(socket, "create_connection", _mock_create_connection)

    def _mock_popen(*args, **kwargs):
        raise Exception("Simulated spawn failure")

    monkeypatch.setattr(subprocess, "Popen", _mock_popen)

    result = asyncio.run(_ensure_portal(timeout=0.1))
    assert result is False
