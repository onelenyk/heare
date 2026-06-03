"""Heare Portal — standalone watchdog HTTP server (port 9780)."""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from pathlib import Path

import aiohttp
from aiohttp import web

from src.config import load_settings
from src.daemon.watch_controls import (
    daemon_pid,
    restart_daemon,
    start_daemon,
    stop_daemon,
)

DAEMON_URL = "http://127.0.0.1:9778"
DEFAULT_PORT = 9780
PID_FILE = Path.home() / ".heare" / "portal.pid"

_INDEX_HTML: str | None = None


def _resolve_index_html() -> str | None:
    for candidate in [
        Path(__file__).resolve().parent / "frontend" / "index.html",
        Path(__file__).resolve().parent.parent / "frontend" / "index.html",
    ]:
        if candidate.exists():
            return candidate.read_text()
    return None


class Portal:
    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self._settings = load_settings()
        self._settings.ensure_dirs()
        self.daemon_starting = False
        self.app = web.Application()
        self._setup_routes()
        self.app.on_shutdown.append(self._on_shutdown)

    # ── Routes ──────────────────────────────────────────────

    def _setup_routes(self):
        self.app.router.add_get("/", self._handle_index)
        self.app.router.add_get("/state", self._proxy_state)
        self.app.router.add_post("/daemon", self._handle_daemon)
        self.app.router.add_get("/api/audio-devices", self._proxy_simple)
        self.app.router.add_post("/api/chrome/launch", self._proxy_simple)
        self.app.router.add_get("/api/tools", self._proxy_simple)

    # ── Handlers ────────────────────────────────────────────

    async def _handle_index(self, request):
        self._maybe_start_daemon()
        global _INDEX_HTML
        if _INDEX_HTML is None:
            _INDEX_HTML = _resolve_index_html()
        if _INDEX_HTML is not None:
            return web.Response(text=_INDEX_HTML, content_type="text/html")
        return web.Response(
            text="<h1>heare portal</h1><p>frontend not found</p>",
            content_type="text/html",
        )

    async def _proxy_state(self, request):
        if not self._is_running():
            self._maybe_start_daemon()
            return web.json_response(
                {"running": False, "starting": self.daemon_starting}
            )
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5)
        ) as session:
            try:
                async with session.get(f"{DAEMON_URL}/state") as resp:
                    return web.Response(
                        body=await resp.read(),
                        status=resp.status,
                        content_type="application/json",
                    )
            except Exception as e:
                return web.json_response(
                    {"running": False, "starting": False, "error": str(e)},
                    status=502,
                )

    async def _proxy_simple(self, request):
        if not self._is_running():
            self._maybe_start_daemon()
            return web.json_response(
                {"error": "daemon not running", "starting": self.daemon_starting},
                status=503,
            )
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5)
        ) as session:
            try:
                data = await request.read() if request.can_read_body else None
                async with session.request(
                    method=request.method,
                    url=f"{DAEMON_URL}{request.path}",
                    data=data,
                ) as resp:
                    return web.Response(
                        body=await resp.read(),
                        status=resp.status,
                        content_type="application/json",
                    )
            except Exception as e:
                return web.json_response({"error": str(e)}, status=502)

    async def _handle_daemon(self, request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        action = body.get("action", "")
        try:
            if action == "start":
                if self._is_running():
                    return web.json_response(
                        {
                            "ok": True,
                            "action": "noop",
                            "message": "already running",
                        }
                    )
                await asyncio.to_thread(start_daemon, self._settings)
                return web.json_response({"ok": True, "action": "started"})
            if action == "stop":
                await asyncio.to_thread(stop_daemon, self._settings)
                return web.json_response({"ok": True, "action": "stopped"})
            if action == "restart":
                await asyncio.to_thread(restart_daemon, self._settings)
                return web.json_response({"ok": True, "action": "restarted"})
            return web.json_response(
                {"ok": False, "error": f"unknown action: {action}"}, status=400
            )
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    # ── Daemon helpers ──────────────────────────────────────

    def _is_running(self) -> bool:
        return daemon_pid(self._settings) is not None

    def _maybe_start_daemon(self):
        if not self._is_running() and not self.daemon_starting:
            self.daemon_starting = True
            asyncio.create_task(self._bg_start())

    async def _bg_start(self):
        try:
            await asyncio.to_thread(start_daemon, self._settings)
        finally:
            self.daemon_starting = False

    # ── Lifecycle ───────────────────────────────────────────

    async def _on_shutdown(self, app):
        if PID_FILE.exists():
            PID_FILE.unlink(missing_ok=True)

    def write_pid_file(self):
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()))


# ── CLI ─────────────────────────────────────────────────────


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args(argv)

    if args.stop:
        if not PID_FILE.exists():
            print("portal not running (no pid file)")
            return 0
        pid_str = PID_FILE.read_text().strip()
        try:
            pid = int(pid_str)
        except ValueError:
            print(f"portal pid file corrupted: {pid_str!r}")
            PID_FILE.unlink(missing_ok=True)
            return 1
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"sent SIGTERM to portal pid {pid}")
        except ProcessLookupError:
            print(f"portal pid {pid} not found")
        PID_FILE.unlink(missing_ok=True)
        return 0

    portal = Portal(port=args.port)
    portal.write_pid_file()
    print(f"heare portal listening on http://127.0.0.1:{args.port}")
    web.run_app(portal.app, host="127.0.0.1", port=args.port, handle_signals=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
