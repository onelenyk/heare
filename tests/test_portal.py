"""Tests for _ensure_portal() portal startup logic."""
from __future__ import annotations

import http.server
import socket
import sys
import threading
import time


def test_ensure_portal_returns_true_when_port_open() -> None:
    """_ensure_portal returns True without spawning when port 9780 is already open."""
    from src.main import _ensure_portal

    server = http.server.HTTPServer(("127.0.0.1", 9780), http.server.SimpleHTTPRequestHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    # Give the server a moment to start listening
    time.sleep(0.3)
    try:
        assert _ensure_portal() is True
    finally:
        server.shutdown()
        server.server_close()


def test_ensure_portal_unfrozen_path() -> None:
    """_ensure_portal works when not frozen (does not raise, returns bool)."""
    frozen = getattr(sys, "frozen", None)
    try:
        if hasattr(sys, "frozen"):
            del sys.frozen
        from src.main import _ensure_portal

        result = _ensure_portal(timeout=1.0)
        assert isinstance(result, bool)
    finally:
        if frozen is not None:
            sys.frozen = frozen


def test_ensure_portal_handles_socket_errors_gracefully(monkeypatch) -> None:
    """_ensure_portal returns False when port is closed and subprocess fails."""
    from src.main import _ensure_portal
    import subprocess as sp

    # Simulate: port is closed
    def _mock_connect(*args, **kwargs):
        raise OSError("Connection refused")

    monkeypatch.setattr(socket, "create_connection", _mock_connect)

    # Simulate: subprocess spawn fails
    def _mock_popen(*args, **kwargs):
        raise Exception("Simulated spawn failure")

    monkeypatch.setattr(sp, "Popen", _mock_popen)

    result = _ensure_portal(timeout=0.1)
    assert result is False
