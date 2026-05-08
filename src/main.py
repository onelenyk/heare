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

    # Refresh Claude capabilities (skills + MCPs) in the background.
    from src.daemon.claude_capabilities import refresh_capabilities

    asyncio.create_task(
        refresh_capabilities(
            settings.capabilities_file,
            workspace_dir=settings.workspace_dir,
            max_age_hours=settings.capabilities_max_age_hours,
        )
    )

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

        context_builder = ContextBuilder(store, settings, conversation_manager)

        speaker_gallery = None
        speaker_model = None
        speaker_namer = None
        if settings.speaker_id_enabled:
            try:
                from src.voice.speaker import id as _sid_mod
                from src.voice.speaker.gallery import SpeakerGallery as _Gallery

                speaker_gallery = _Gallery.load(settings.speakers_file)
                loop = asyncio.get_running_loop()
                speaker_model = await loop.run_in_executor(
                    None, _sid_mod.load_model
                )
                logger.info(
                    "Speaker subsystem: gallery loaded (%d speakers), ECAPA model ready",
                    len(speaker_gallery.list_speakers()),
                )
            except Exception:
                logger.exception(
                    "Speaker subsystem init failed — continuing without diarization"
                )
                speaker_gallery = None
                speaker_model = None

        if speaker_gallery is not None and speaker_model is not None:
            from src.voice.speaker.namer import maybe_build_namer

            context_builder.speaker_gallery = speaker_gallery

            speaker_namer = maybe_build_namer(
                settings, speaker_gallery, settings.openrouter_api_key
            )
            if speaker_namer is not None:
                logger.info(
                    "Speaker namer: enabled, model=%s",
                    settings.speaker_namer_model,
                )

        namer_enqueue = speaker_namer.enqueue if speaker_namer is not None else None

        (
            pipeline,
            transcription_gate,
            tts_cache,
            indication,
            llm_service,
            language_state,
        ) = await build_pipeline(
            settings,
            store,
            context_builder,
            persona=persona,
            conversation_manager=conversation_manager,
            speaker_gallery=speaker_gallery,
            speaker_model=speaker_model,
            namer_enqueue=namer_enqueue,
        )

        from pipecat.frames.frames import TTSSpeakFrame  # noqa: E402

        from src.voice.tts.edge import synthesize_to_pcm
        from src.voice.tts.phrases import FIXED_PHRASES

        try:
            await tts_cache.warmup(
                FIXED_PHRASES,
                lambda text: synthesize_to_pcm(
                    text, settings.tts_voice, settings.tts_sample_rate
                ),
            )
        except Exception as e:
            logger.warning("TTS cache warmup failed (non-fatal): %s", e)

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

        namer_task = None
        if speaker_namer is not None:
            namer_task = asyncio.create_task(speaker_namer.run())
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
            namer_task=namer_task,
        )
    finally:
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
    runner, pipeline, warmup=None, *, namer_task=None,
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

    stop_waiter = loop.create_task(stop_event.wait())
    watch_set = {pipeline_task, stop_waiter}
    if warmup_task is not None:
        watch_set.add(warmup_task)
    if namer_task is not None:
        watch_set.add(namer_task)
    try:
        done, _ = await asyncio.wait(watch_set, return_when=asyncio.FIRST_COMPLETED)
    finally:
        if warmup is not None:
            warmup.stop()
        background_tasks = [pipeline_task, stop_waiter]
        if warmup_task is not None:
            background_tasks.append(warmup_task)
        if namer_task is not None:
            background_tasks.append(namer_task)
        for task in background_tasks:
            if not task.done():
                task.cancel()
        named_tasks = [(pipeline_task, "pipeline")]
        if warmup_task is not None:
            named_tasks.append((warmup_task, "warmup"))
        if namer_task is not None:
            named_tasks.append((namer_task, "speaker-namer"))
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


def _cmd_provider(args: argparse.Namespace) -> int:
    settings = load_settings()
    provider = args.provider_name
    settings.provider_file.parent.mkdir(parents=True, exist_ok=True)
    settings.provider_file.write_text(provider)
    print(f"LLM provider set to {provider} (effective on next user utterance)")
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

    from src.voice.speaker import id as speaker_id_mod
    from src.voice.speaker.gallery import (
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


def _cmd_test_recognizer(args: argparse.Namespace) -> int:
    """Interactive speaker recognition tester."""
    try:
        from . import test_recognizer
    except ImportError as e:
        print(f"test-recognizer requires pyaudio: {e}")
        print("Install with: pip install pyaudio")
        return 1

    import asyncio

    sys.argv = ["test-recognizer"]
    if args.threshold:
        sys.argv.extend(["--threshold", str(args.threshold)])
    if args.duration:
        sys.argv.extend(["--duration", str(args.duration)])

    try:
        asyncio.run(test_recognizer.main())
    except (KeyboardInterrupt, EOFError):
        print("\nExiting...")
    return 0


def _cmd_speakers_list(args: argparse.Namespace) -> int:
    from src.voice.speaker.gallery import SpeakerGallery

    settings = load_settings()
    gallery = SpeakerGallery.load(settings.speakers_file)
    ids = gallery.list_speakers()
    if not ids:
        print("(no speakers enrolled)")
        return 0
    for sid in ids:
        entry = gallery.get_entry(sid) or {}
        label = entry.get("label", "?")
        turn_count = entry.get("turn_count", 0)
        updated = entry.get("updated_at", "?")
        print(f"{sid}\t{label}\tturn_count={turn_count}\tupdated={updated}")
    return 0


def _cmd_speakers_info(args: argparse.Namespace) -> int:
    import numpy as np

    from src.voice.speaker.gallery import SpeakerGallery

    settings = load_settings()
    gallery = SpeakerGallery.load(settings.speakers_file)
    entry = gallery.get_entry(args.speaker_id)
    if not entry:
        print(f"speaker not found: {args.speaker_id}")
        return 1
    embeddings = entry.get("embeddings") or []
    ref_count = len(embeddings)
    centroid = gallery.get_centroid(args.speaker_id)
    centroid_norm = float(np.linalg.norm(centroid)) if centroid is not None else 0.0
    print(f"id: {args.speaker_id}")
    print(f"label: {entry.get('label', '?')}")
    print(f"created_at: {entry.get('created_at', '?')}")
    print(f"updated_at: {entry.get('updated_at', '?')}")
    print(f"turn_count: {entry.get('turn_count', 0)}")
    print(f"ref_count: {ref_count}")
    print(f"centroid_norm: {centroid_norm:.4f}")
    return 0


def _cmd_speakers_rm(args: argparse.Namespace) -> int:
    from src.voice.speaker.gallery import SpeakerGallery

    settings = load_settings()
    if not args.yes:
        print("pass --yes to confirm")
        return 1
    gallery = SpeakerGallery.load(settings.speakers_file)
    if not gallery.remove_speaker(args.speaker_id):
        print(f"speaker not found: {args.speaker_id}")
        return 1
    print(f"removed: {args.speaker_id}")
    return 0


def _cmd_speakers_rename(args: argparse.Namespace) -> int:
    from src.voice.speaker.gallery import LabelValidationError, SpeakerGallery

    settings = load_settings()
    gallery = SpeakerGallery.load(settings.speakers_file)
    try:
        ok = gallery.rename_speaker(args.speaker_id, args.new_label)
    except LabelValidationError as e:
        print(f"invalid label: {e}")
        return 1
    if not ok:
        print(f"speaker not found: {args.speaker_id}")
        return 1
    stored_label = gallery.get_label(args.speaker_id)
    print(f"renamed: {args.speaker_id} -> {stored_label}")
    return 0


def _cmd_speakers(args: argparse.Namespace) -> int:
    sub = args.speakers_cmd
    if sub == "list":
        return _cmd_speakers_list(args)
    if sub == "info":
        return _cmd_speakers_info(args)
    if sub == "rm":
        return _cmd_speakers_rm(args)
    if sub == "rename":
        return _cmd_speakers_rename(args)
    if sub == "audit":
        return _cmd_speakers_audit(args)
    print("unknown speakers subcommand")
    return 1


def _cmd_speakers_audit(args: argparse.Namespace) -> int:
    from src.voice.speaker.gallery import SpeakerGallery

    settings = load_settings()
    gallery = SpeakerGallery.load(settings.speakers_file)
    target_ids = [args.speaker_id] if args.speaker_id else gallery.list_speakers()
    if not target_ids:
        print("(no speakers enrolled)")
        return 0
    any_missing = False
    for sid in target_ids:
        report = gallery.audit(sid)
        if report is None:
            print(f"{sid}: not found")
            any_missing = True
            continue
        status = "DRIFT" if report["enrollment_cos_floor_hit"] else "OK"
        print(
            f"{sid} refs={report['ref_count']} "
            f"centroid[min={report['min_cos_vs_centroid']:.3f} "
            f"mean={report['mean_cos_vs_centroid']:.3f} "
            f"max={report['max_cos_vs_centroid']:.3f}] "
            f"enrollment[mean={report['mean_cos_vs_enrollment']:.3f}] "
            f"{status}"
        )
    return 1 if any_missing else 0


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
    prov_p.add_argument("provider_name", choices=["openrouter", "zai"])

    sub.add_parser("reset-session", help="Backup session.json and start fresh")
    sub.add_parser("reset-identity", help="Backup identity.json and regenerate")

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

    enroll_p = sub.add_parser(
        "enroll-owner",
        help="Record ~15s of your voice and set as the owner reference",
    )
    enroll_p.add_argument("--duration", type=int, default=15, help="Recording seconds")
    enroll_p.add_argument("--label", type=str, default="owner", help="Human label")

    test_p = sub.add_parser("test-recognizer", help="Interactive speaker recognition tester")
    test_p.add_argument("--threshold", type=float, default=None, help="Override match threshold")
    test_p.add_argument("--duration", type=int, default=None, help="Recording duration (ms)")

    speakers_p = sub.add_parser("speakers", help="Manage the speaker gallery")
    speakers_sub = speakers_p.add_subparsers(dest="speakers_cmd", required=True)
    speakers_sub.add_parser("list", help="List enrolled speakers")
    info_p = speakers_sub.add_parser("info", help="Show details for a speaker")
    info_p.add_argument("speaker_id", help="Speaker id (e.g. owner)")
    rm_p = speakers_sub.add_parser("rm", help="Remove a speaker")
    rm_p.add_argument("speaker_id")
    rm_p.add_argument("--yes", action="store_true", help="Confirm removal")
    rename_p = speakers_sub.add_parser("rename", help="Rename a speaker label")
    rename_p.add_argument("speaker_id")
    rename_p.add_argument("new_label")
    audit_p = speakers_sub.add_parser("audit", help="Cosine drift report")
    audit_p.add_argument("speaker_id", nargs="?", default=None)

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
    if cmd == "provider":
        return _cmd_provider(args)
    if cmd == "reset-session":
        return _cmd_reset_session(args)
    if cmd == "reset-identity":
        return _cmd_reset_identity(args)
    if cmd == "set-passphrase":
        return _cmd_set_wake_word(args)
    if cmd == "setup":
        return _cmd_setup(args)
    if cmd == "watch":
        return _cmd_watch(args)
    if cmd == "logs":
        return _cmd_logs(args)
    if cmd == "enroll-owner":
        return _cmd_enroll_owner(args)
    if cmd == "test-recognizer":
        return _cmd_test_recognizer(args)
    if cmd == "speakers":
        return _cmd_speakers(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
