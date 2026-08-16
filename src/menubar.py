"""Heare — macOS menu bar app.

One process: menu bar GUI + web server + voice agent.
"""

from __future__ import annotations

import asyncio
import os
import queue
import sys
import threading
import time
from pathlib import Path

import httpx
import rumps
from aiohttp import web

PORT = 9780
URL = f"http://127.0.0.1:{PORT}"
POLL_INTERVAL = 1.5
LOCK_FILE = Path.home() / ".heare" / "menubar.pid"
_FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"
_DIST_DIR = _FRONTEND_DIR / "dist"
if getattr(sys, "frozen", False):
    _MEIPASS = Path(sys._MEIPASS)
    _FRONTEND_DIR = _MEIPASS / "src" / "frontend"
    _DIST_DIR = _FRONTEND_DIR / "dist"
_INDEX_HTML: str | None = None


def _api_headers(token: str) -> dict:
    """Authorization header for the local API, which now requires one.

    Every route but the dashboard shell answers 401 without it — see the
    module docstring of src/api.py.
    """
    return {"Authorization": f"Bearer {token}"} if token else {}


def _resolve_index_html() -> str | None:
    for candidate in [_DIST_DIR / "index.html", _FRONTEND_DIR / "index.html"]:
        if candidate.exists():
            return candidate.read_text()
    return None


def _acquire_lock() -> bool:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_FILE.exists():
        try:
            old_pid = int(LOCK_FILE.read_text().strip())
            os.kill(old_pid, 0)
            return False
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    LOCK_FILE.write_text(str(os.getpid()))
    return True


def _release_lock():
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


async def _handle_index(request):
    global _INDEX_HTML
    if _INDEX_HTML is None:
        _INDEX_HTML = _resolve_index_html()
    if _INDEX_HTML:
        return web.Response(text=_INDEX_HTML, content_type="text/html")
    return web.Response(
        text="<h1>heare</h1><p>frontend not found</p>",
        content_type="text/html",
    )


# ── Hub ───────────────────────────────────────────────────


class HeareMenuBar(rumps.App):
    def __init__(self):
        super().__init__(name="Heare", title="🎧", quit_button=None)
        self._cmd_queue: queue.Queue = queue.Queue()
        self._status_lock = threading.Lock()
        self._status: dict = {"running": False, "mode": "?", "error": None}
        # Filled in by _serve() from settings once the config is loaded.
        self._api_token: str = ""
        self._build_menu()
        threading.Thread(target=self._asyncio_main, daemon=False).start()

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with self._status_lock:
                if self._status.get("ready"):
                    break
            time.sleep(0.3)
        else:
            rumps.alert("Heare failed to start within 15 seconds.")
            os._exit(1)

        rumps.Timer(self._update_ui, POLL_INTERVAL).start()

    def _build_menu(self):
        self.menu.clear()
        self.menu.add(rumps.MenuItem("Status: starting…", callback=None))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Start", callback=self._on_start))
        self.menu.add(rumps.MenuItem("Stop", callback=self._on_stop))
        self.menu.add(rumps.MenuItem("Open Dashboard", callback=self._on_open))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Quit Heare", callback=self._on_quit))

    # ── Thread-safe status (asyncio → UI) ──────────────

    def _set_status(self, **kwargs):
        with self._status_lock:
            self._status.update(kwargs)

    def _get_status(self) -> dict:
        with self._status_lock:
            return dict(self._status)

    # ── Asyncio thread (daemon=False — if it dies, app dies) ──

    def _asyncio_main(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve())
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            loop.close()
        _release_lock()
        os._exit(0)

    async def _serve(self):
        import logging.handlers  # noqa: F401 — force PyInstaller bundling

        from dotenv import load_dotenv
        from src.config import load_settings
        from src.state import State
        from src.api import API

        for env_path in (
            Path.home() / ".heare" / ".env",
            Path(__file__).resolve().parent.parent / ".env",
        ):
            if env_path.exists():
                load_dotenv(env_path)
                break
        else:
            load_dotenv(Path(__file__).resolve().parent.parent / ".env")

        settings = load_settings()
        settings.ensure_dirs()
        # load_settings() generates ~/.heare/api_token on first run; the
        # status poll below is a client of our own API and needs it.
        self._api_token = settings.api_token or ""

        app = web.Application()
        app.router.add_get("/", _handle_index)
        if _DIST_DIR.exists():
            app.router.add_static(
                "/assets/", _DIST_DIR / "assets", show_index=False
            )

        state = State(str(settings.db_path))
        api = API(
            state, settings,
            daemon_control=(
                self._start_pipeline,
                self._stop_pipeline,
                self._restart_pipeline,
            ),
        )
        api.register_routes(app)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", PORT, reuse_address=True)

        try:
            await site.start()
        except OSError as e:
            if e.errno == 48:
                raise RuntimeError(
                    "Port 9780 is in use. Another Heare may be running."
                ) from e
            raise RuntimeError(str(e)) from e

        self._set_status(ready=True)

        # Let the agent's stop_daemon / restart_daemon tools cycle the
        # pipeline without killing this process, so the menu bar survives
        # "вимикайся" and can start it again.
        from src.daemon import control as daemon_control

        daemon_control.set_host_hooks(
            stop=self._stop_pipeline, restart=self._restart_pipeline
        )

        # Auto-start the voice pipeline on launch — without this the app
        # sits "stopped" until someone clicks Start in the menu.
        self._cmd_queue.put(("start",))

        pipeline_task: asyncio.Task | None = None

        try:
            while True:
                try:
                    cmd = self._cmd_queue.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.1)
                    continue

                try:
                    if cmd[0] == "start":
                        if pipeline_task is not None and not pipeline_task.done():
                            continue
                        pipeline_task = asyncio.create_task(
                            self._run_pipeline(settings, state, api)
                        )
                        # A cancelled task raises CancelledError from
                        # .exception() — asking without checking turned
                        # every ordinary Stop into a crash inside the
                        # callback, which killed the menubar's loop.
                        def _finished(t: "asyncio.Task") -> None:
                            error = None
                            if not t.cancelled():
                                exc = t.exception()
                                error = str(exc) if exc else None
                            self._set_status(error=error, running=False)

                        pipeline_task.add_done_callback(_finished)
                        self._set_status(running=True, error=None)

                    elif cmd[0] == "stop":
                        if pipeline_task is not None and not pipeline_task.done():
                            pipeline_task.cancel()
                            try:
                                await pipeline_task
                            except asyncio.CancelledError:
                                pass
                        await state.set("running", "false")
                        state.set_cache_only("voice_state", {
                            "state": "idle", "since_ts": time.time(),
                            "last_partial": None, "last_final": None,
                        })
                        state.set_cache_only("agent_state", {
                            "state": "idle", "since_ts": time.time(),
                        })
                        pipeline_task = None
                        self._set_status(running=False)

                    elif cmd[0] == "restart":
                        self._cmd_queue.put(("stop",))
                        await asyncio.sleep(0.5)
                        self._cmd_queue.put(("start",))

                    elif cmd[0] == "shutdown":
                        if pipeline_task is not None and not pipeline_task.done():
                            pipeline_task.cancel()
                            try:
                                await pipeline_task
                            except asyncio.CancelledError:
                                pass
                        await runner.cleanup()
                        return

                except Exception:
                    import logging as _log
                    _log.getLogger("heare.menubar").exception(
                        "Command %s failed", cmd[0]
                    )
                    self._set_status(error=str(cmd))

        finally:
            if pipeline_task is not None and not pipeline_task.done():
                pipeline_task.cancel()
                try:
                    await pipeline_task
                except asyncio.CancelledError:
                    pass

    # ── Pipeline ────────────────────────────────────────

    async def _run_pipeline(self, settings, state, api):
        if getattr(sys, "frozen", False):
            os.environ["PATH"] = (
                "/opt/homebrew/bin:/usr/local/bin:"
                + os.environ.get("PATH", "")
            )

        from src.config import load_settings as _reload
        from src.main import _setup_logging, _build_and_run_daemon

        settings = _reload()
        settings.ensure_dirs()
        _setup_logging(settings.log_dir)

        project_dir = (
            sys._MEIPASS
            if getattr(sys, "frozen", False)
            else str(Path(__file__).resolve().parent.parent)
        )

        await state.set("running", "true")
        try:
            await _build_and_run_daemon(
                settings, state, project_dir, api, handle_signals=False,
            )
        finally:
            await state.set("running", "false")

    # Dashboard /daemon endpoint control hooks. api.py awaits the start
    # callback and calls stop/restart synchronously — signatures must match.

    async def _start_pipeline(self):
        self._cmd_queue.put(("start",))

    def _stop_pipeline(self):
        self._cmd_queue.put(("stop",))

    def _restart_pipeline(self):
        self._cmd_queue.put(("restart",))

    # ── UI (main thread — rumps callbacks + timer) ──────

    def _update_ui(self, _=None):
        status = self._get_status()
        error = status.get("error")
        running = status.get("running", False)

        if error:
            self.title = "⚠️"
            self.menu["Status: starting…"].title = f"Heare — error"
            self.menu["Start"].set_callback(self._on_start)
            self.menu["Stop"].set_callback(None)
            return

        try:
            resp = httpx.get(
                f"{URL}/state", timeout=3, headers=_api_headers(self._api_token)
            )
            data = resp.json()
            mode = data.get("mode", "?")
        except Exception:
            mode = "?"

        if running:
            self.title = "🟢"
            self.menu["Status: starting…"].title = f"Heare — running ({mode})"
            self.menu["Start"].set_callback(None)
            self.menu["Stop"].set_callback(self._on_stop)
        else:
            self.title = "🔴"
            self.menu["Status: starting…"].title = "Heare — stopped"
            self.menu["Start"].set_callback(self._on_start)
            self.menu["Stop"].set_callback(None)

    def _open_dashboard(self):
        import webbrowser
        webbrowser.open(URL)

    def _on_start(self, _):
        self._cmd_queue.put(("start",))

    def _on_stop(self, _):
        self._cmd_queue.put(("stop",))

    def _on_open(self, _):
        self._open_dashboard()

    def _on_quit(self, _):
        self._cmd_queue.put(("shutdown",))


def main():
    if not _acquire_lock():
        sys.exit(0)
    HeareMenuBar().run()


if __name__ == "__main__":
    main()
