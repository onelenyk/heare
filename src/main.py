"""heare CLI entry point.

Pipecat imports are deferred to `_cmd_start` so `--help` and admin
subcommands work on machines without portaudio.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
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


def _ensure_workspace_mcp(workspace_dir: Path) -> None:
    """Seed workspace/.mcp.json from the user's global ~/.claude.json on first run.

    Without this, claude -p invocations from heare's workspace start with no MCP
    servers — sessions can succeed inconsistently depending on resume state.
    Users can edit the resulting file to add or remove servers.
    """
    import json

    workspace_dir.mkdir(parents=True, exist_ok=True)
    target = workspace_dir / ".mcp.json"
    if target.exists():
        return
    global_cfg = Path.home() / ".claude.json"
    mcp_servers: dict = {}
    if global_cfg.exists():
        try:
            data = json.loads(global_cfg.read_text())
            mcp_servers = data.get("mcpServers", {}) or {}
        except (OSError, json.JSONDecodeError):
            pass
    target.write_text(json.dumps({"mcpServers": mcp_servers}, indent=2))
    logger.info(
        "seeded %s with %d MCP server(s) from global config",
        target,
        len(mcp_servers),
    )


async def _cmd_start(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    from .claude_cli import ClaudeCLI
    from .context import ContextBuilder
    from .heartbeat import HeartbeatTask, WarmupTask
    from .identity import ensure_identity, render_persona
    from .pipeline import build_pipeline
    from .storage import TranscriptStore

    load_dotenv(Path(__file__).parent.parent / ".env")
    settings = load_settings()
    settings.ensure_dirs()
    _setup_logging(settings.log_dir)
    _ensure_workspace_mcp(settings.workspace_dir)

    settings.pid_file.write_text(str(os.getpid()))
    store: TranscriptStore | None = None
    try:
        store = TranscriptStore(settings.db_path)
        await store.init()
        await store.purge_older_than(settings.transcript_retention_days)

        claude_cli = ClaudeCLI(settings)
        version = await claude_cli.version()
        logger.info("claude CLI version: %s", version)

        identity = await ensure_identity(claude_cli, settings)
        persona_template = (
            Path(__file__).parent.parent / "prompts" / "persona.txt"
        ).read_text()
        claude_cli.persona = render_persona(persona_template, identity)
        logger.info("I am %s %s", identity["name"], identity["emoji"])

        context_builder = ContextBuilder(store, settings)
        pipeline, decider, tts_cache = await build_pipeline(
            settings, claude_cli, store, context_builder
        )

        # Warm up the TTS cache with FIXED_PHRASES so cancel/confirm/etc. play
        # instantly. Failures are non-fatal — falls back to live TTS.
        from .decider import FIXED_PHRASES
        from .tts_edge import synthesize_to_pcm

        try:
            await tts_cache.warmup(
                FIXED_PHRASES,
                lambda text: synthesize_to_pcm(
                    text, settings.tts_voice, settings.tts_sample_rate
                ),
            )
        except Exception as e:
            logger.warning("TTS cache warmup failed (non-fatal): %s", e)

        heartbeat = HeartbeatTask(decider, settings.heartbeat_interval_minutes)
        warmup = WarmupTask(
            voice=settings.tts_voice,
            interval_seconds=settings.warmup_interval_seconds,
        )

        from pipecat.pipeline.runner import PipelineRunner  # noqa: E402

        runner = PipelineRunner()
        await run_until_stopped(runner, pipeline, heartbeat, warmup)
    finally:
        if store is not None:
            await store.close()
        if settings.pid_file.exists():
            settings.pid_file.unlink()
        logger.info("heare stopped")
    return 0


async def run_until_stopped(runner, pipeline, heartbeat, warmup=None) -> None:
    loop = asyncio.get_running_loop()
    pipeline_task = loop.create_task(runner.run(pipeline))
    heartbeat_task = loop.create_task(heartbeat.run())
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

    stop_waiter = loop.create_task(stop_event.wait())
    watch_set = {pipeline_task, heartbeat_task, stop_waiter}
    if warmup_task is not None:
        watch_set.add(warmup_task)
    try:
        done, _ = await asyncio.wait(watch_set, return_when=asyncio.FIRST_COMPLETED)
    finally:
        heartbeat.stop()
        if warmup is not None:
            warmup.stop()
        background_tasks = [pipeline_task, heartbeat_task, stop_waiter]
        if warmup_task is not None:
            background_tasks.append(warmup_task)
        for task in background_tasks:
            if not task.done():
                task.cancel()
        named_tasks = [(pipeline_task, "pipeline"), (heartbeat_task, "heartbeat")]
        if warmup_task is not None:
            named_tasks.append((warmup_task, "warmup"))
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


def _cmd_mode(args: argparse.Namespace) -> int:
    settings = load_settings()
    mode = Mode(args.mode_name)
    settings.mode_file.parent.mkdir(parents=True, exist_ok=True)
    settings.mode_file.write_text(mode.value)
    print(f"mode set to {mode.value}")
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
    from .identity import reset_identity

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


def _cmd_enroll_owner(args: argparse.Namespace) -> int:
    settings = load_settings()
    if not settings.speaker_id_enabled:
        print(
            "speaker_id_enabled is False — set it to True in ~/.heare/config.toml"
            " before enrolling an owner."
        )
        return 1
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError as e:
        print(f"enroll-owner requires sounddevice + numpy: {e}")
        return 1

    from . import speaker_id as speaker_id_mod
    from .speaker_gallery import (
        LabelValidationError,
        SpeakerGallery,
        sanitize_label,
    )

    try:
        label = sanitize_label(args.label)
    except LabelValidationError as e:
        print(f"invalid label: {e}")
        return 1

    duration = max(1, int(args.duration))
    sample_rate = 16000
    print(f"Recording {duration}s at {sample_rate} Hz — speak now.")
    for i in range(3, 0, -1):
        print(f"  {i}...")
        time.sleep(1)
    recording = sd.rec(
        int(duration * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
    )
    sd.wait()
    pcm = recording.astype(np.int16).tobytes()

    print("Loading ECAPA model (first run downloads ~17MB) ...")
    model = speaker_id_mod.load_model()
    vector = speaker_id_mod.embed(pcm, sample_rate, model)

    settings.speakers_file.parent.mkdir(parents=True, exist_ok=True)
    gallery = SpeakerGallery.load(settings.speakers_file)
    gallery.enroll_owner(vector, label=label)
    print(
        f"enrolled owner='{label}' from {duration}s of audio "
        f"(embedding dim {vector.shape[0]})"
    )
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
        description="Proactive ambient voice AI assistant powered by Claude Code",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("start", help="Start the voice daemon in the foreground")
    sub.add_parser("stop", help="Stop the running daemon via pid file")
    sub.add_parser("status", help="Show daemon status")

    mode_p = sub.add_parser("mode", help="Set the current mode (hot-reloaded)")
    mode_p.add_argument("mode_name", choices=[m.value for m in Mode])

    sub.add_parser("reset-session", help="Backup session.json and start fresh")
    sub.add_parser("reset-identity", help="Backup identity.json and regenerate")

    watch_p = sub.add_parser("watch", help="Live status view (Ctrl+C to exit)")
    watch_p.add_argument("--interval", type=float, default=0.5, help="Refresh seconds")
    watch_p.add_argument("--once", action="store_true", help="Print once and exit")

    logs_p = sub.add_parser("logs", help="Tail the daemon log")
    logs_p.add_argument("-f", "--follow", action="store_true", help="Stream new entries")
    logs_p.add_argument("-n", "--lines", type=int, default=40, help="How many lines")

    enroll_p = sub.add_parser(
        "enroll-owner",
        help="Record ~15s of your voice and set as the owner reference",
    )
    enroll_p.add_argument("--duration", type=int, default=15, help="Recording seconds")
    enroll_p.add_argument("--label", type=str, default="owner", help="Human label")
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
        return _cmd_mode(args)
    if cmd == "reset-session":
        return _cmd_reset_session(args)
    if cmd == "reset-identity":
        return _cmd_reset_identity(args)
    if cmd == "watch":
        return _cmd_watch(args)
    if cmd == "logs":
        return _cmd_logs(args)
    if cmd == "enroll-owner":
        return _cmd_enroll_owner(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
