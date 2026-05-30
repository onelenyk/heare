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
import sys
import time
from pathlib import Path

from .config import Mode, load_settings


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


async def _cmd_start(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    from src.store.context import ContextBuilder
    from src.daemon.heartbeat import WarmupTask
    from src.agent.identity import build_openrouter_bootstrap, ensure_identity, render_persona
    from src.pipeline.build import build_pipeline
    from src.store.storage import TranscriptStore

    load_dotenv(Path(__file__).parent.parent / ".env")
    settings = load_settings()
    settings.ensure_dirs()
    _setup_logging(settings.log_dir)
    from src.daemon.workspace import ensure_workspace_mcp
    ensure_workspace_mcp(settings.workspace_dir)

    project_dir = str(Path(__file__).parent.parent.resolve())

    # Onboarding gate — must run AFTER ensure_workspace_mcp so upgrading
    # users whose ~/.claude.json has MCPs but who haven't run heare since
    # the upgrade get their workspace seeded before the migration check.
    from src.daemon.onboarding import is_onboarded, list_pending, migrate_existing_install

    migrate_existing_install(settings)
    if not is_onboarded(settings):
        pending = list_pending(settings)
        print("heare not fully set up. Pending steps:")
        for step in pending:
            print(f"  - {step.id}: {step.title}")
        print("\nRun `heare setup` to continue.")
        return 1

    if settings.pid_file.exists():
        try:
            existing_pid = int(settings.pid_file.read_text().strip())
            try:
                os.kill(existing_pid, 0)
                logger.error(
                    "Daemon already running (PID %s). Stop it first with: heare stop",
                    existing_pid,
                )
                print(f"❌ Error: Daemon already running (PID {existing_pid})")
                print("   Stop it first: heare stop")
                return 1
            except OSError:
                logger.info("Removing stale PID file %s", settings.pid_file)
                settings.pid_file.unlink()
        except (ValueError, OSError) as e:
            logger.warning("Invalid PID file %s: %s. Removing.", settings.pid_file, e)
            try:
                settings.pid_file.unlink()
            except OSError:
                pass

    settings.pid_file.write_text(str(os.getpid()))

    if not settings.openrouter_api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set — copy .env.example to .env and fill it in"
        )

    store: TranscriptStore | None = None
    try:
        store = TranscriptStore(settings.db_path)
        await store.init()
        await store.purge_older_than(settings.transcript_retention_days)

        identity_bootstrap = build_openrouter_bootstrap(
            api_key=settings.openrouter_api_key,
            model=settings.openrouter_model,
            timeout=settings.openrouter_timeout_seconds,
        )
        identity = await ensure_identity(identity_bootstrap, settings)
        persona_template = (
            Path(__file__).parent.parent / "prompts" / "persona.txt"
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

        context_builder = ContextBuilder(
            store, settings, conversation_manager,
            project_dir=project_dir,
        )

        from src.state import State

        state = State(settings.db_path)
        await state.init()

        (
            pipeline,
            transcription_gate,
            tts_cache,
            indication,
            llm_service,
            language_state,
            mcp_bridge,
        ) = await build_pipeline(
            settings,
            store,
            context_builder,
            persona=persona,
            state=state,
            conversation_manager=conversation_manager,
            project_dir=project_dir,
        )

        from src.api import API

        api = API(state, settings)
        await api.start()
        logger.info("HTTP API server on 127.0.0.1:9778")

        from pipecat.frames.frames import TTSSpeakFrame  # noqa: E402

        from src.voice.tts.edge import synthesize_to_pcm
        from src.voice.tts.phrases import FIXED_PHRASES

        # Fire-and-forget: cache populates in background while the pipeline
        # comes up. First greeting may hit edge-tts live (~1-3s) before the
        # cache is ready; all subsequent identical phrases hit the cache.
        tts_cache_warmup_task = asyncio.create_task(
            tts_cache.warmup(
                FIXED_PHRASES,
                lambda text: synthesize_to_pcm(
                    text, settings.tts_voice, settings.tts_sample_rate
                ),
            )
        )

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
                # Push from llm_service downstream so the frame goes straight
                # to TTS without traversing the user_aggregator (which can
                # consume/transform unknown frames).
                await llm_service.push_frame(TTSSpeakFrame(greeting))
                logger.info("startup greeting queued: %r", greeting)
            except Exception:
                logger.exception("startup greeting push failed (non-fatal)")

        bridge = None
        bridge_task = None
        if settings.browser_bridge_enabled:
            try:
                from src.agent.browser_bridge import BrowserBridge, set_bridge
                bridge = BrowserBridge(settings)
                set_bridge(bridge)
                bridge_task = asyncio.create_task(bridge.start(), name="browser-bridge")
            except Exception:  # noqa: BLE001
                logger.exception("browser_bridge failed to start (continuing without it)")
                bridge = None
                bridge_task = None

        asyncio.create_task(_push_greeting())

        # Text-injection poller: a separate process (e.g. the watch
        # dashboard) drops .txt files into ``settings.inject_dir`` and we
        # push them as TranscriptionFrame, taking the same path as STT
        # output through the transcription_gate.
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

        from pipecat.pipeline.runner import PipelineRunner  # noqa: E402

        runner = PipelineRunner()
        await run_until_stopped(
            runner,
            pipeline,
            warmup,
            settings=settings,
            bridge_task=bridge_task,
        )
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
        from .config import load_settings
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
                    from .config import load_settings

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
    from .config import HEARE_HOME  # noqa: E402

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
    from .config import HEARE_HOME, write_browser_bridge_token

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
    from .watch import run_watch

    settings = load_settings()
    return run_watch(settings, interval=args.interval, once=args.once)
    

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


def _cmd_setup(args: argparse.Namespace) -> int:
    """Run or inspect heare onboarding."""
    from src.daemon.onboarding import (
        STEPS,
        list_pending,
        migrate_existing_install,
        record_done,
        reset,
        step_status,
    )
    from src.daemon.workspace import ensure_workspace_mcp

    settings = load_settings()
    settings.ensure_dirs()
    # Seed workspace before migration so upgrading users' existing
    # ~/.claude.json is reflected in workspace/.mcp.json on first
    # `heare setup`.
    ensure_workspace_mcp(settings.workspace_dir)
    migrate_existing_install(settings)

    if args.reset:
        if not args.yes:
            print(
                "This will wipe ~/.heare/onboarding.json and "
                "~/.heare/capabilities.json. workspace/.mcp.json is preserved."
            )
            try:
                ans = input("Continue? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\naborted")
                return 1
            if ans not in ("y", "yes"):
                print("aborted")
                return 1
        reset(settings)
        print("onboarding state wiped — run `heare setup` to re-onboard")
        return 0

    if args.status:
        for step, done in step_status(settings):
            mark = "✓" if done else "✗"
            print(f"  {mark} {step.id:<24s} {step.title}")
        if list_pending(settings):
            print("\nRun `heare setup` to walk pending steps.")
        else:
            print("\nAll steps complete. Run `heare start` to launch the daemon.")
        return 0

    if args.confirm:
        step = next((s for s in STEPS if s.id == args.confirm), None)
        if step is None:
            print(f"unknown step: {args.confirm}")
            print(f"available: {', '.join(s.id for s in STEPS)}")
            return 1
        if step.requires_attestation and not args.yes:
            print(
                f"warning: {step.id!r} normally requires interactive attestation. "
                "Pass --yes to confirm non-interactively, or run `heare setup` "
                "(without --confirm) to walk it interactively."
            )
            return 1
        try:
            step.on_confirm(settings)
        except Exception as exc:  # noqa: BLE001
            print(f"step {step.id!r} failed: {exc}")
            return 1
        record_done(settings, step.id)
        print(f"✓ {step.id}")
        return 0

    pending = list_pending(settings)
    if not pending:
        print("Already onboarded. Run `heare start` to launch the daemon.")
        return 0

    print(f"Heare onboarding: {len(pending)} step(s) remaining.\n")
    for step in pending:
        print(f"--- {step.title} ---")
        print(step.instructions)
        try:
            ans = input("\n[enter to confirm, q to quit] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\naborted — partial progress saved")
            return 1
        if ans == "q":
            print("aborted — partial progress saved")
            return 1
        try:
            step.on_confirm(settings)
        except Exception as exc:  # noqa: BLE001
            print(f"step {step.id!r} failed: {exc}")
            return 1
        record_done(settings, step.id)
        print(f"✓ {step.id}\n")

    print("Onboarding complete. Run `heare start` to launch the daemon.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="heare",
        description="Proactive ambient voice AI assistant powered by Claude Code",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("start", help="Start the voice daemon in the foreground")
    sub.add_parser("stop", help="Stop the running daemon via pid file")
    sub.add_parser("status", help="Show daemon status")

    mode_p = sub.add_parser("mode", help="Set the current mode (hot-reloaded)")
    mode_p.add_argument("mode_name", choices=[m.value for m in Mode])

    prov_p = sub.add_parser("provider", help="Set the LLM provider (hot-reloaded)")
    prov_p.add_argument("provider_name", choices=["openrouter", "zai", "opencode", "deepseek"])

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

    watch_p = sub.add_parser("watch", help="Live status view (Ctrl+C to exit)")
    watch_p.add_argument("--interval", type=float, default=0.5, help="Refresh seconds")
    watch_p.add_argument("--once", action="store_true", help="Print once and exit")

    setup_p = sub.add_parser("setup", help="Run or inspect heare onboarding")
    setup_p.add_argument("--status", action="store_true",
                         help="Show step status without prompting")
    setup_p.add_argument("--confirm", metavar="ID",
                         help="Mark one specific step done (advanced)")
    setup_p.add_argument("--reset", action="store_true",
                         help="Wipe onboarding state")
    setup_p.add_argument("--yes", action="store_true",
                         help="Skip confirmation (--reset) or allow attestation override (--confirm)")

    logs_p = sub.add_parser("logs", help="Tail the daemon log")
    logs_p.add_argument("-f", "--follow", action="store_true", help="Stream new entries")
    logs_p.add_argument("-n", "--lines", type=int, default=40, help="How many lines")

    return parser


def main(argv: list[str] | None = None) -> int:
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
    if cmd == "setup":
        return _cmd_setup(args)
    if cmd == "watch":
        return _cmd_watch(args)
    if cmd == "logs":
        return _cmd_logs(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
