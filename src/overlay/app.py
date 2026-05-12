"""pywebview launcher — single-process overlay entry point.

Runs uvicorn in a daemon thread on a free localhost port, then hands
control to pywebview on the main thread (required on macOS). The window
is frameless and always-on-top; the pywebview event loop owns the main
thread until the user closes the window.
"""
from __future__ import annotations

import logging
import socket
import threading


logger = logging.getLogger("heare.overlay")


def _pick_free_port() -> int:
    """Bind to port 0 to let the kernel pick a free port, then release it.

    Race-able in theory; in practice uvicorn re-binds the same port
    within milliseconds and we never share the loopback with other
    processes that care.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server_thread(app, host: str, port: int) -> threading.Thread:
    import uvicorn

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(
        target=server.run, name="overlay-uvicorn", daemon=True
    )
    thread.start()
    return thread


def run() -> int:
    """Entry point — returns a Unix-style exit code."""
    try:
        import webview  # type: ignore[import-not-found]
    except ImportError:
        print(
            "overlay: pywebview is not installed. "
            "Run: uv pip install -e '.[overlay]'"
        )
        return 1

    from src.config import load_settings
    from src.overlay.server import bind_host, create_app

    settings = load_settings()
    app = create_app(settings)

    host = bind_host()
    port = _pick_free_port()
    _start_server_thread(app, host, port)

    url = f"http://{host}:{port}/"
    logger.info("overlay: window opening at %s", url)

    webview.create_window(
        title="heare",
        url=url,
        width=420,
        height=520,
        frameless=True,
        on_top=True,
        resizable=True,
    )
    webview.start()
    return 0


__all__ = ["run"]
