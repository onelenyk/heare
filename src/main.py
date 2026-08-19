"""heare CLI entry point.

Heavy imports are deferred into the subcommands so `--help` and the
admin paths work on a machine without portaudio.
"""

from __future__ import annotations

import os



import argparse
import asyncio
import logging
import logging.handlers
import signal
import sys
import time
from pathlib import Path

from typing import TYPE_CHECKING

from src.config import Mode, load_settings

if TYPE_CHECKING:  # names used only in annotations
    from src.api import API
    from src.config import Settings
    from src.state import State


logger = logging.getLogger("heare.main")

_INDEX_HTML: str | None = None




DAEMON_LOG_MAX_BYTES = 10 * 1024 * 1024
DAEMON_LOG_BACKUPS = 3


def _setup_logging(log_dir: Path) -> logging.handlers.RotatingFileHandler:
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "daemon.log",
        maxBytes=DAEMON_LOG_MAX_BYTES,
        backupCount=DAEMON_LOG_BACKUPS,
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for existing in list(root.handlers):
        if isinstance(
            existing, (logging.handlers.RotatingFileHandler, logging.StreamHandler)
        ):
            root.removeHandler(existing)
    root.addHandler(handler)
    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(stream)
    return handler


async def _build_and_run_daemon(
    settings: "Settings",
    state: "State",
    project_dir: str,
    api: "API",
    *,
    handle_signals: bool = True,
) -> None:
    """Boot and run the full daemon: store, identity, pipeline, bridge, cleanup.

    This is the shared boot sequence used by both the CLI ``start`` command
    and the macOS menubar.  Callers are responsible for setting up the web
    server + API + frontend before calling this function.

    Parameters
    ----------
    settings:
        Fully-loaded settings (``load_env`` / ``load_settings`` already
        called by the caller).
    state:
        State object.  ``state.init()`` MUST NOT have been called yet (this
        function calls it after pipeline build to avoid SQLite lock
        contention).
    project_dir:
        Path to the project root (or ``sys._MEIPASS`` when frozen).
    api:
        API instance (already running).  ``api.state`` will be set to *state*
        after pipeline build.
    handle_signals:
        If True (CLI daemon), install SIGINT/SIGTERM/SIGHUP handlers.
        If False (menubar), skip them — the menubar manages its own lifecycle.
    """
    from src.daemon.spine_engine import run_spine_daemon

    await run_spine_daemon(settings, state, api, handle_signals=handle_signals)




async def _cmd_start(args: argparse.Namespace) -> int:
    """Start the daemon with web server + frontend on :9780."""
    if getattr(sys, "frozen", False):
        os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get(
            "PATH", ""
        )

    from src.config import load_env

    load_env()
    settings = load_settings()
    settings.ensure_dirs()
    _setup_logging(settings.log_dir)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets.server").setLevel(logging.WARNING)

    project_dir = (
        sys._MEIPASS
        if getattr(sys, "frozen", False)
        else str(Path(__file__).parent.parent.resolve())
    )

    # File lock — OS-level guard against multiple instances.
    import fcntl

    try:
        lock_fd = os.open(settings.pid_file, os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        if getattr(sys, "frozen", False):
            logger.info("Daemon already running (lock held) — opening dashboard")
            import webbrowser

            webbrowser.open("http://127.0.0.1:9780/")
            return 0
        print("❌ Error: Daemon already running. Stop it first: heare stop")
        return 1
    os.write(lock_fd, str(os.getpid()).encode())
    os.fsync(lock_fd)

    settings.pid_file.write_text(str(os.getpid()))

    # Build web app with API routes + frontend static files.
    from aiohttp import web

    from src.api import API
    from src.state import State

    app = web.Application()
    app.router.add_get("/", _serve_frontend)
    _FRONTEND_DIST = Path(project_dir) / "src" / "frontend" / "dist"
    if _FRONTEND_DIST.exists():
        app.router.add_static(
            "/assets/", str(_FRONTEND_DIST / "assets"), show_index=False
        )

    state = State(settings.db_path)
    api = API(state, settings)
    api.register_routes(app)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 9780, reuse_address=True)
    await site.start()
    logger.info("Web server on http://127.0.0.1:9780")

    await _build_and_run_daemon(
        settings, state, project_dir, api, handle_signals=True
    )

    # Cleanup web server.
    await runner.cleanup()
    if settings.pid_file.exists():
        settings.pid_file.unlink()
    return 0


async def _serve_frontend(request):
    """Serve index.html for the SPA frontend."""
    global _INDEX_HTML
    if _INDEX_HTML is None:
        _FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"
        for candidate in [
            _FRONTEND_DIST / "index.html",
            Path(__file__).parent / "frontend" / "index.html",
        ]:
            if candidate.exists():
                _INDEX_HTML = candidate.read_text()
                break
    if _INDEX_HTML:
        from aiohttp import web

        return web.Response(text=_INDEX_HTML, content_type="text/html")
    from aiohttp import web

    return web.Response(
        text="<h1>heare</h1><p>frontend not found — run `npm run build` in src/frontend/</p>",
        content_type="text/html",
    )




def _cmd_stop(args: argparse.Namespace) -> int:
    settings = load_settings()
    if not settings.pid_file.exists():
        print("heare is not running (no pid file)")
        return 0
    pid = int(settings.pid_file.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        settings.pid_file.unlink(missing_ok=True)
        print("heare was not running; stale pid file removed")
        return 0
    for _ in range(30):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            print(f"heare ({pid}) stopped")
            return 0
        time.sleep(0.1)
    os.kill(pid, signal.SIGKILL)
    print(f"heare ({pid}) force-killed after timeout")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    settings = load_settings()
    running = False
    if settings.pid_file.exists():
        pid = int(settings.pid_file.read_text().strip())
        try:
            os.kill(pid, 0)
            running = True
        except ProcessLookupError:
            settings.pid_file.unlink(missing_ok=True)
    print(f"heare running: {running}")
    print(f"mode:         {settings.mode.value}")
    print(f"db:           {settings.db_path}")
    print(f"session:      {settings.session_file}")
    return 0


async def _cmd_mode(args: argparse.Namespace) -> int:
    from src.state import State

    settings = load_settings()
    mode = Mode(args.mode_name)
    state = State(settings.db_path)
    await state.init()
    await state.set("mode", mode.value)
    print(f"mode set to {mode.value}")
    return 0


async def _cmd_provider(args: argparse.Namespace) -> int:
    """Choose which model answers — the same three writes POST /provider makes.

    This used to set the state entry only, and print that it would take
    effect on the next utterance. It never did: the engine resolves its
    provider from Settings, which is loaded from config.toml, and nothing
    read the state entry at all.
    """
    from src.agent.llm.providers import PROVIDERS
    from src.config import write_config_toml_values
    from src.state import State

    provider = args.provider_name
    if provider not in PROVIDERS:
        print(f"unknown provider {provider!r} — one of {', '.join(PROVIDERS)}")
        return 1

    settings = load_settings()
    state = State(settings.db_path)
    await state.init()
    await state.set("provider", provider)
    write_config_toml_values({"llm_provider": provider})
    print(f"LLM provider set to {provider} (effective on next user utterance)")
    return 0


def _cmd_audio_input(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.audio_input_device_file.parent.mkdir(parents=True, exist_ok=True)
    settings.audio_input_device_file.write_text(args.name)
    print(f"audio input device set to {args.name!r} — hot-reloaded")
    return 0


def _cmd_audio_output(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.audio_output_device_file.parent.mkdir(parents=True, exist_ok=True)
    settings.audio_output_device_file.write_text(args.name)
    print(f"audio output device set to {args.name!r} — hot-reloaded")
    return 0


def _cmd_allow_installs(args: argparse.Namespace) -> int:
    """Turn installing skills and MCP servers on or off.

    Was `set-passphrase`, which took a secret word, called a function
    named after the wake word, and set neither: the value was never
    compared to anything, only tested for emptiness.
    """
    from src.config import HEARE_HOME, set_capability_install_enabled  # noqa: E402

    choice = args.state.strip().lower()
    if choice not in ("on", "off"):
        print("usage: allow-installs on|off")
        return 1

    enabled = choice == "on"
    set_capability_install_enabled(enabled)

    # Mark onboarding as complete
    (HEARE_HOME / ".onboarded").touch()

    state = "allowed" if enabled else "blocked"
    print(f"installing skills and MCP servers is now {state} — restart daemon to apply")
    return 0


def _cmd_rotate_browser_token(args: argparse.Namespace) -> int:
    """Generate a new browser-bridge token, persist it, and update the
    convenience file. Daemon must be restarted for the new token to take
    effect."""
    import secrets
    from src.config import HEARE_HOME, write_browser_bridge_token

    settings = load_settings()
    token = secrets.token_urlsafe(32)
    write_browser_bridge_token(settings, token)

    token_path = HEARE_HOME / "browser_bridge.token"
    token_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(token_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")

    print(
        "New browser bridge token written. Paste into the extension options "
        "page. If daemon is running, restart it for the new token to take "
        "effect."
    )
    print(f"Token file: {token_path}")
    return 0


def _cmd_reset_session(args: argparse.Namespace) -> int:
    from src.config import backup_session_file  # noqa: E402

    settings = load_settings()
    backup = backup_session_file(settings)
    if backup is None:
        print("no session file to reset")
        return 0
    print(f"session backed up to {backup}")
    return 0


def _cmd_reset_identity(args: argparse.Namespace) -> int:
    from src.agent.identity import reset_identity

    settings = load_settings()
    backup = reset_identity(settings)
    if backup is None:
        print("no identity file to reset")
    else:
        print(f"identity backed up to {backup}")
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    print("heare watch has been removed. Use the web UI at http://127.0.0.1:9780")
    return 0


def _cmd_portal(args: argparse.Namespace) -> int:
    print("'portal' command is deprecated. Use 'menubar' instead:")
    print("  uv run python -m src.main menubar")
    return 0


def _cmd_logs(args: argparse.Namespace) -> int:
    settings = load_settings()
    log_file = settings.log_dir / "daemon.log"
    if not log_file.exists():
        print(f"no log file at {log_file}")
        return 0
    tail_args = ["tail"]
    if args.follow:
        tail_args.append("-f")
    tail_args.extend(["-n", str(args.lines), str(log_file)])
    os.execvp("tail", tail_args)
    return 0  # unreachable


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="heare",
        description="Proactive ambient voice AI assistant",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("start", help="Start the voice daemon in the foreground")
    sub.add_parser("stop", help="Stop the running daemon via pid file")
    sub.add_parser("status", help="Show daemon status")

    mode_p = sub.add_parser("mode", help="Set the current mode (hot-reloaded)")
    mode_p.add_argument("mode_name", choices=[m.value for m in Mode])

    prov_p = sub.add_parser("provider", help="Set the LLM provider (hot-reloaded)")
    from src.agent.llm.providers import all_keys

    prov_p.add_argument("provider_name", choices=all_keys())

    audio_in_p = sub.add_parser(
        "audio-input", help="Set the audio input device (hot-reloaded)"
    )
    audio_in_p.add_argument("name", help="Device name substring (e.g. AirPods Pro)")

    audio_out_p = sub.add_parser(
        "audio-output", help="Set the audio output device (hot-reloaded)"
    )
    audio_out_p.add_argument("name", help="Device name substring (e.g. AirPods Pro)")

    sub.add_parser("reset-session", help="Backup session.json and start fresh")
    sub.add_parser("reset-identity", help="Backup identity.json and regenerate")
    sub.add_parser(
        "rotate-browser-token",
        help="Generate a new browser-bridge token (restart daemon to apply)",
    )

    installs_p = sub.add_parser(
        "allow-installs",
        help="Allow or block installing skills and MCP servers (restart required)",
    )
    installs_p.add_argument("state", choices=("on", "off"), help="on or off")

    _ = sub.add_parser(
        "watch", help="(removed) TUI dashboard — use web UI at http://127.0.0.1:9780"
    )

    sub.add_parser("menubar", help="Run macOS menu bar controller")

    logs_p = sub.add_parser("logs", help="Tail the daemon log")
    logs_p.add_argument(
        "-f", "--follow", action="store_true", help="Stream new entries"
    )
    logs_p.add_argument("-n", "--lines", type=int, default=40, help="How many lines")

    return parser


def main(argv: list[str] | None = None) -> int:
    # When run as a bundled .app (double-click from Finder), default to
    # `start` so the daemon launches instead of showing argparse errors.
    if argv is None and len(sys.argv) <= 1 and getattr(sys, "frozen", False):
        argv = ["menubar"]
    parser = build_parser()
    args = parser.parse_args(argv)
    cmd = args.cmd
    if cmd == "start":
        return asyncio.run(_cmd_start(args))
    if cmd == "stop":
        return _cmd_stop(args)
    if cmd == "status":
        return _cmd_status(args)
    if cmd == "mode":
        return asyncio.run(_cmd_mode(args))
    if cmd == "provider":
        return asyncio.run(_cmd_provider(args))
    if cmd == "audio-input":
        return _cmd_audio_input(args)
    if cmd == "audio-output":
        return _cmd_audio_output(args)
    if cmd == "reset-session":
        return _cmd_reset_session(args)
    if cmd == "reset-identity":
        return _cmd_reset_identity(args)
    if cmd == "rotate-browser-token":
        return _cmd_rotate_browser_token(args)
    if cmd == "allow-installs":
        return _cmd_allow_installs(args)
    if cmd == "watch":
        return _cmd_watch(args)
    if cmd == "portal":
        return _cmd_portal(args)
    if cmd == "menubar":
        from src.menubar import main as menubar_main

        return menubar_main()
    if cmd == "logs":
        return _cmd_logs(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
