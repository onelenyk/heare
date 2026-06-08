"""heare CLI entry point.

Pipecat imports are deferred to `_cmd_start` so `--help` and admin
subcommands work on machines without portaudio.
"""
from __future__ import annotations

import os

# Silence transformers' "PyTorch was not found" advice triggered by
# pipecat's smart-turn import path. Must be set before transformers loads.
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

import argparse
import asyncio
import logging
import logging.handlers
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from src.config import Mode, load_settings


logger = logging.getLogger("heare.main")

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
        if isinstance(existing, (logging.handlers.RotatingFileHandler, logging.StreamHandler)):
            root.removeHandler(existing)
    root.addHandler(handler)
    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(stream)
    return handler


def _ensure_portal(timeout: float = 10.0) -> bool:
    if not getattr(sys, "frozen", False):
        return True

    def _port_open() -> bool:
        try:
            with socket.create_connection(("127.0.0.1", 9780), timeout=0.5):
                return True
        except (OSError, socket.timeout):
            return False

    if _port_open():
        return True

    try:
        # Detach so the portal survives the daemon if it dies, and so this
        # .app launcher can exit without killing the portal it started.
        subprocess.Popen(
            [sys.executable, "portal"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        logger.warning(f"Failed to spawn portal subprocess: {e}")
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_open():
            return True
        time.sleep(0.3)
    return False


async def _cmd_start(args: argparse.Namespace) -> int:
    if getattr(sys, "frozen", False):
        os.environ["PATH"] = (
            "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")
        )

    from dotenv import load_dotenv

    from src.store.context import ContextBuilder
    from src.daemon.heartbeat import WarmupTask
    from src.agent.identity import ensure_identity, render_persona
    from src.agent.llm.providers import PROVIDERS, get_available, make_identity_bootstrap
    from src.pipeline.build import build_pipeline
    from src.store.storage import TranscriptStore

    for env_path in (
        Path.home() / ".heare" / ".env",
        Path(__file__).parent.parent / ".env",
    ):
        if env_path.exists():
            load_dotenv(env_path)
            break
    else:
        load_dotenv(Path(__file__).parent.parent / ".env")
    settings = load_settings()
    settings.ensure_dirs()
    _setup_logging(settings.log_dir)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets.server").setLevel(logging.WARNING)
    from src.daemon.workspace import ensure_workspace_mcp
    ensure_workspace_mcp(settings.workspace_dir)

    project_dir = (
        sys._MEIPASS if getattr(sys, "frozen", False)
        else str(Path(__file__).parent.parent.resolve())
    )

    # File lock on PID file — OS-level guard against multiple instances.
    # Auto-releases on process death (crash, SIGKILL, etc.). Race-free.
    import fcntl
    try:
        lock_fd = os.open(settings.pid_file, os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        if getattr(sys, "frozen", False):
            logger.info("Daemon already running (lock held) — opening dashboard")
            _ensure_portal()
            import webbrowser
            webbrowser.open("http://127.0.0.1:9780/")
            return 0
        print("❌ Error: Daemon already running. Stop it first: heare stop")
        return 1
    os.write(lock_fd, str(os.getpid()).encode())
    os.fsync(lock_fd)

    # Start API server early (no state yet — avoids SQLite lock contention
    # with setup API polling). State is init'd after pipeline build below.
    from src.api import API
    api = API(None, settings)
    await api.start()
    logger.info("HTTP API server on 127.0.0.1:9778")

    _ensure_portal()

    available = get_available(settings)
    if not available:
        logger.warning("No API keys configured — serving setup page")
        print("\n  ⚙  No API keys found. Setup page opened in your browser.\n")
        print("  Enter your API keys in the browser to continue.\n")

        while not get_available(settings):
            await asyncio.sleep(1)
            load_dotenv(Path.home() / ".heare" / ".env", override=True)
            settings = load_settings()
            settings.ensure_dirs()

        print("✅ API keys configured — starting daemon...\n")

    settings.pid_file.write_text(str(os.getpid()))

    # Create state object (no .init() — avoids SQLite lock contention with
    # setup API polling. Init'd after pipeline build below.)
    from src.state import State
    state = State(settings.db_path)

    store: TranscriptStore | None = None
    try:
        store = TranscriptStore(settings.db_path)
        await store.init()

        active_cfg = PROVIDERS.get(settings.llm_provider, PROVIDERS["deepseek"])
        api_key = getattr(settings, active_cfg.api_key_attr)
        identity_factory = make_identity_bootstrap(
            active_cfg, api_key, active_cfg.default_model, active_cfg.timeout,
        )
        identity = await ensure_identity(identity_factory, settings)
        persona_template = (
            Path(project_dir) / "prompts" / "persona.txt"
        ).read_text()
        persona = render_persona(persona_template, identity)
        logger.info("I am %s %s", identity["name"], identity["emoji"])

        conversation_manager = None
        if settings.conversation_memory_enabled:
            from src.store.conversation import ConversationManager

            conversation_manager = ConversationManager(store)
            try:
                await conversation_manager.hydrate_action_log(
                    since_ts=time.time() - settings.conversation_idle_seconds,
                )
            except Exception:
                logger.exception(
                    "action_log hydrate failed (non-fatal) — starting empty"
                )

        # Initialize memory backend (pluggable — sqlite by default)
        from src.memory.factory import create_memory_backend
        memory_backend = create_memory_backend(settings)
        await memory_backend.initialize()
        logger.info("Memory backend: %s initialized", settings.memory_backend)

        context_builder = ContextBuilder(
            store, settings, conversation_manager,
            project_dir=project_dir,
            memory_backend=memory_backend,
        )

        try:
            (
                pipeline,
                transcription_gate,
                tts_cache,
                indication,
                llm_service,
                language_state,
                mcp_bridge,
                agent_manager,
            ) = await build_pipeline(
                settings,
                store,
                context_builder,
                persona=persona,
                state=state,
                conversation_manager=conversation_manager,
                project_dir=project_dir,
                memory_backend=memory_backend,
            )
        except Exception:
            logger.exception("Pipeline build failed — running in dashboard-only mode")
            pipeline = None
            transcription_gate = None
            tts_cache = None
            indication = None
            llm_service = None
            language_state = None
            mcp_bridge = None

        # Init state now that pipeline is built — avoids SQLite lock contention
        # during setup polling (API was started with state=None).
        await state.init()
        api.state = state

        if pipeline is not None:
            from pipecat.frames.frames import TTSSpeakFrame  # noqa: E402

            async def _push_greeting() -> None:
                await asyncio.sleep(1.0)
                from src.voice.indication.core import IndicationKind, get_indication

                _ind = get_indication()
                if _ind is not None:
                    _ind.notify(
                        IndicationKind.DAEMON_STARTED,
                        body=f"{identity.get('name') or settings.wake_word} ready",
                    )
                greeting_name = identity.get("name") or settings.wake_word
                _greetings = {"en": "online", "uk": "на зв'язку", "ru": "на связи"}
                _greeting_suffix = _greetings.get(settings.groq_language, "online")
                greeting = f"{greeting_name} {_greeting_suffix}"
                try:
                    await llm_service.push_frame(TTSSpeakFrame(greeting))
                    logger.info("startup greeting queued: %r", greeting)
                except Exception:
                    logger.exception("startup greeting push failed (non-fatal)")

            asyncio.create_task(_push_greeting())

            from src.pipeline.stages.text_injector import make_transcription_pusher, run_injector_loop

            inject_pusher = make_transcription_pusher(
                transcription_gate,
                user_id="injected",
                language=settings.groq_language
                if settings.groq_language not in ("auto", "")
                else None,
            )
            asyncio.create_task(
                run_injector_loop(settings.inject_dir, inject_pusher)
            )

            warmup = WarmupTask(
                voice=settings.tts_voice,
                interval_seconds=settings.warmup_interval_seconds,
            )
        else:
            tts_cache = None
            indication = None
            warmup = None

        bridge = None
        bridge_task = None
        if settings.browser_bridge_enabled:
            try:
                from src.agent.browser_bridge import BrowserBridge, set_bridge
                bridge = BrowserBridge(settings)
                set_bridge(bridge)
                bridge_task = asyncio.create_task(bridge.start(), name="browser-bridge")
            except Exception:
                logger.exception("browser_bridge failed to start (continuing without it)")
                bridge = None
                bridge_task = None

        if pipeline is not None:
            from pipecat.pipeline.runner import PipelineRunner  # noqa: E402
            runner = PipelineRunner()
            await run_until_stopped(
                runner,
                pipeline,
                warmup,
                settings=settings,
                bridge_task=bridge_task,
            )
        else:
            stop_event = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                try:
                    loop.add_signal_handler(sig, stop_event.set)
                except NotImplementedError:
                    pass
            await stop_event.wait()
    finally:
        bridge = locals().get("bridge")
        if bridge is not None:
            try:
                await bridge.stop()
            except Exception as e:  # noqa: BLE001
                logger.warning("bridge.stop failed (non-fatal): %s", e)
            try:
                from src.agent.browser_bridge import set_bridge
                set_bridge(None)
            except Exception:  # noqa: BLE001
                pass
        mcp_bridge = locals().get("mcp_bridge")
        if mcp_bridge is not None:
            try:
                await mcp_bridge.aclose()
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "mcp_bridge.aclose failed (non-fatal): %s", e
                )
        api = locals().get("api")
        if api is not None:
            try:
                await api.stop()
            except Exception as e:  # noqa: BLE001
                logger.warning("api.stop failed (non-fatal): %s", e)
        ind = locals().get("indication")
        if ind is not None:
            try:
                from src.voice.indication.core import IndicationKind

                ind.notify(IndicationKind.DAEMON_SHUTDOWN)
            except Exception:  # noqa: BLE001
                pass
            try:
                await ind.aclose()
            except Exception as e:  # noqa: BLE001
                logger.warning("indication.aclose failed (non-fatal): %s", e)
        if store is not None:
            await store.close()
        if settings.pid_file.exists():
            settings.pid_file.unlink()
        mgr = locals().get("agent_manager")
        if mgr is not None:
            try:
                await mgr.shutdown()
            except Exception as e:
                logger.warning("agent_manager shutdown failed (non-fatal): %s", e)
        mb = locals().get("memory_backend")
        if mb is not None:
            try:
                await mb.close()
                logger.info("Memory backend closed")
            except Exception:
                logger.warning("Memory backend close failed (non-fatal)")
        logger.info("heare stopped")
    return 0


async def run_until_stopped(
    runner, pipeline, warmup=None, *, settings=None, bridge_task=None,
) -> None:
    loop = asyncio.get_running_loop()
    pipeline_task = loop.create_task(runner.run(pipeline))
    warmup_task = loop.create_task(warmup.run()) if warmup is not None else None
    stop_event = asyncio.Event()

    def _handle_signal(sig_name: str) -> None:
        logger.info("received %s — shutting down", sig_name)
        stop_event.set()

    installed_signals: list[signal.Signals] = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle_signal, sig.name)
            installed_signals.append(sig)
        except NotImplementedError:
            pass

    def _handle_sighup() -> None:
        logger.info("received SIGHUP — reloading indication settings")
        from src.config import load_settings
        from src.voice.indication.core import get_indication

        ind = get_indication()
        if ind is None:
            logger.info("SIGHUP: no indication facade reachable; ignored")
            return
        try:
            loop.create_task(ind.reload(load_settings().indication))
        except Exception as e:  # noqa: BLE001
            logger.warning("SIGHUP indication reload failed: %s", e)

    try:
        loop.add_signal_handler(signal.SIGHUP, _handle_sighup)
    except (NotImplementedError, AttributeError):
        pass

    # Audio device hot-reload: every 3s check if the device files changed
    # and if so, reconfigure the transport's output stream.
    audio_dev_task: asyncio.Task | None = None
    if settings is not None:

        async def _watch_audio_devices() -> None:
            from src.pipeline.build import reload_audio_device

            last_mtime_out = 0.0
            last_mtime_in = 0.0
            while True:
                try:
                    await asyncio.sleep(3)
                    from src.config import load_settings

                    s = load_settings()
                    # Check output device file
                    if s.audio_output_device_file.exists():
                        mtime = s.audio_output_device_file.stat().st_mtime
                        if mtime > last_mtime_out:
                            last_mtime_out = mtime
                            if reload_audio_device(s):
                                logger.info(
                                    "audio device: output switched to %r",
                                    s.audio_output_device,
                                )
                    # Check input device file
                    if s.audio_input_device_file.exists():
                        mtime_in = s.audio_input_device_file.stat().st_mtime
                        if mtime_in > last_mtime_in:
                            last_mtime_in = mtime_in
                            from src.pipeline.build import reload_audio_input_device

                            if reload_audio_input_device(s):
                                logger.info(
                                    "audio device: input switched to %r",
                                    s.audio_input_device,
                                )
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.exception("audio device watcher error (non-fatal)")

        audio_dev_task = asyncio.create_task(_watch_audio_devices())

    stop_waiter = loop.create_task(stop_event.wait())
    watch_set = {pipeline_task, stop_waiter}
    if warmup_task is not None:
        watch_set.add(warmup_task)
    if bridge_task is not None:
        watch_set.add(bridge_task)
    if audio_dev_task is not None:
        watch_set.add(audio_dev_task)
    try:
        done, _ = await asyncio.wait(watch_set, return_when=asyncio.FIRST_COMPLETED)
    finally:
        if warmup is not None:
            warmup.stop()
        background_tasks = [pipeline_task, stop_waiter]
        if warmup_task is not None:
            background_tasks.append(warmup_task)
        if bridge_task is not None:
            background_tasks.append(bridge_task)
        if audio_dev_task is not None:
            background_tasks.append(audio_dev_task)
        for task in background_tasks:
            if not task.done():
                task.cancel()
        named_tasks = [(pipeline_task, "pipeline")]
        if warmup_task is not None:
            named_tasks.append((warmup_task, "warmup"))
        if bridge_task is not None:
            named_tasks.append((bridge_task, "browser-bridge"))
        if audio_dev_task is not None:
            named_tasks.append((audio_dev_task, "audio-device"))
        for task, name in named_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("%s task crashed during shutdown", name)
        for sig in installed_signals:
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, ValueError):
                pass


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
    from src.state import State

    settings = load_settings()
    provider = args.provider_name
    state = State(settings.db_path)
    await state.init()
    await state.set("provider", provider)
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


def _cmd_set_wake_word(args: argparse.Namespace) -> int:
    from src.config import HEARE_HOME  # noqa: E402

    word = args.word.strip()
    if not word:
        print("passphrase cannot be empty")
        return 1

    # Write to config.toml
    config_path = HEARE_HOME / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Read existing config or create new
    if config_path.exists():
        content = config_path.read_text()
        # Update or add confirmation_passphrase line
        if "confirmation_passphrase" in content:
            import re
            content = re.sub(r'confirmation_passphrase\s*=\s*".*?"', f'confirmation_passphrase = "{word}"', content)
        else:
            content += f'\nconfirmation_passphrase = "{word}"\n'
    else:
        content = f'confirmation_passphrase = "{word}"\n'

    config_path.write_text(content)

    # Mark onboarding as complete
    (HEARE_HOME / ".onboarded").touch()

    print(f"confirmation passphrase set to '{word}' — restart daemon to apply")
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
    settings = load_settings()
    if not settings.session_file.exists():
        print("no session file to reset")
        return 0
    idx = 0
    while True:
        backup = settings.session_file.with_name(
            f"session_{idx}.backup.json"
        )
        if not backup.exists():
            break
        idx += 1
    settings.session_file.rename(backup)
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
    from src.portal import main as portal_main

    argv = ["--port", str(args.port)]
    if args.stop:
        argv.append("--stop")
    return portal_main(argv)


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

    audio_in_p = sub.add_parser("audio-input", help="Set the audio input device (hot-reloaded)")
    audio_in_p.add_argument("name", help="Device name substring (e.g. AirPods Pro)")

    audio_out_p = sub.add_parser("audio-output", help="Set the audio output device (hot-reloaded)")
    audio_out_p.add_argument("name", help="Device name substring (e.g. AirPods Pro)")

    sub.add_parser("reset-session", help="Backup session.json and start fresh")
    sub.add_parser("reset-identity", help="Backup identity.json and regenerate")
    sub.add_parser(
        "rotate-browser-token",
        help="Generate a new browser-bridge token (restart daemon to apply)",
    )

    set_word_p = sub.add_parser("set-passphrase", help="Set the confirmation passphrase (restart required)")
    set_word_p.add_argument("word", help="Secret word to confirm actions (e.g. авторизую)")

    watch_p = sub.add_parser("watch", help="(removed) TUI dashboard — use web UI at http://127.0.0.1:9780")

    portal_p = sub.add_parser("portal", help="Run watchdog web UI portal")
    portal_p.add_argument("--port", type=int, default=9780)
    portal_p.add_argument("--stop", action="store_true")

    logs_p = sub.add_parser("logs", help="Tail the daemon log")
    logs_p.add_argument("-f", "--follow", action="store_true", help="Stream new entries")
    logs_p.add_argument("-n", "--lines", type=int, default=40, help="How many lines")

    return parser


def main(argv: list[str] | None = None) -> int:
    # When run as a bundled .app (double-click from Finder), default to
    # `start` so the daemon launches instead of showing argparse errors.
    if argv is None and len(sys.argv) <= 1 and getattr(sys, "frozen", False):
        argv = ["start"]
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
    if cmd == "set-passphrase":
        return _cmd_set_wake_word(args)
    if cmd == "watch":
        return _cmd_watch(args)
    if cmd == "portal":
        return _cmd_portal(args)
    if cmd == "logs":
        return _cmd_logs(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
