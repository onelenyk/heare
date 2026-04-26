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
from typing import TYPE_CHECKING

from .config import Mode, load_settings

if TYPE_CHECKING:
    from .claude_backend_common import ClaudeBackend


logger = logging.getLogger("heare.main")

DAEMON_LOG_MAX_BYTES = 10 * 1024 * 1024
DAEMON_LOG_BACKUPS = 3

# Phase A observability — user-facing TTS phrases when actions finish.
_ACTION_SUCCESS_FALLBACK = "Готово."
_ACTION_TIMEOUT_HINT = "Дія не встигла, скасовую."
_ACTION_SUMMARY_MAX_CHARS = 120

# VFA-06: generic per-language sentences for tools that ship no spoken template.
_GENERIC_DONE = {"en": "Done.", "uk": "Готово.", "ru": "Готово."}
_GENERIC_FAILURE = {
    "en": "Action failed.",
    "uk": "Не вдалося виконати.",
    "ru": "Не удалось выполнить.",
}


def _spoken_action_summary(summary: str, scrubber) -> str:
    """Build the one-line Ukrainian phrase spoken after a successful action.

    Empty or scrub-emptied summaries fall back to 'Готово.' so the user
    always hears an acknowledgement.
    """
    flat = " ".join(summary.split())
    if not flat:
        return _ACTION_SUCCESS_FALLBACK
    clipped = flat[:_ACTION_SUMMARY_MAX_CHARS]
    cleaned = scrubber(clipped)
    return cleaned or _ACTION_SUCCESS_FALLBACK


def _action_error_hint(exc: BaseException) -> str:
    """Short, non-leaky Ukrainian phrase describing the action failure."""
    if isinstance(exc, asyncio.TimeoutError):
        return _ACTION_TIMEOUT_HINT
    return f"Не вдалося — {exc.__class__.__name__}."


def _resolve_spoken(result: dict, intent: object, fallback_summary: str) -> str:
    """Resolve the TTS-ready spoken phrase from an action result dict.

    Resolution order (VFA-05 / VFA-06):
    1. result["spoken"] is a non-empty dict  → pick by intent.language, fall back to "en".
    2. result["spoken"] is a non-empty str   → use as-is.
    3. spoken missing/None AND success=True  → generic per-language Done sentence.
    4. spoken missing/None AND success=False → generic per-language failure sentence.
    5. Last resort (direct-tool short output explicitly provided) → fallback_summary.
    """
    spoken = result.get("spoken")
    lang = getattr(intent, "language", "en") or "en"

    if isinstance(spoken, dict) and spoken:
        return spoken.get(lang) or spoken.get("en") or fallback_summary

    if isinstance(spoken, str) and spoken:
        return spoken

    # spoken is None or missing — use generic sentences keyed by language.
    if result.get("success") is True:
        return _GENERIC_DONE.get(lang) or _GENERIC_DONE["en"]

    if result.get("success") is False:
        return _GENERIC_FAILURE.get(lang) or _GENERIC_FAILURE["en"]

    # Last resort: the caller's explicitly-supplied fallback (e.g. short direct-tool output).
    return fallback_summary


async def _persist_action_outcome(store, intent, status: str, spoken: str) -> None:
    """Phase B-0: record the finished action and its spoken outcome.

    Idempotent on failure (each call is independently try/except'd) so a
    broken store never interrupts the TTS pipeline. `store=None` short-circuits
    for the legacy code path with no persistence.
    """
    if store is None:
        return
    if intent.decision_id is not None:
        try:
            await store.log_action(intent.decision_id, status, spoken)
        except Exception:
            logger.exception(
                "action row persist failed id=%d (non-fatal)", intent.id
            )
    try:
        await store.log_decision(
            intent.transcript_id,
            {"type": "speak", "reply": spoken},
        )
    except Exception:
        logger.exception(
            "action speech persist failed id=%d (non-fatal)", intent.id
        )


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
    if mcp_servers:
        names = ", ".join(mcp_servers.keys())
        logger.warning(
            "Auto-authorized MCP servers from ~/.claude.json: %s. "
            "All servers in workspace/.mcp.json are now callable by the agent. "
            "Review and remove any unwanted entries.",
            names,
        )


async def _cmd_start(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

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

    # Prevent multiple daemon instances
    if settings.pid_file.exists():
        try:
            existing_pid = int(settings.pid_file.read_text().strip())
            # Check if process is still running
            try:
                os.kill(existing_pid, 0)  # Signal 0 doesn't kill, just checks existence
                logger.error(
                    "Daemon already running (PID %s). Stop it first with: heare stop",
                    existing_pid,
                )
                print(f"❌ Error: Daemon already running (PID {existing_pid})")
                print("   Stop it first: heare stop")
                return 1
            except OSError:
                # Process not running, stale PID file
                logger.info("Removing stale PID file %s", settings.pid_file)
                settings.pid_file.unlink()
        except (ValueError, OSError) as e:
            logger.warning("Invalid PID file %s: %s. Removing.", settings.pid_file, e)
            try:
                settings.pid_file.unlink()
            except OSError:
                pass

    settings.pid_file.write_text(str(os.getpid()))

    from .agent_sdk_cli import AgentSDKCLI
    _backend: "ClaudeBackend" = AgentSDKCLI(settings)

    store: TranscriptStore | None = None
    try:
        store = TranscriptStore(settings.db_path)
        await store.init()
        await store.purge_older_than(settings.transcript_retention_days)

        async with _backend as claude_cli:
            version = await claude_cli.version()
            logger.info("claude backend: %s", version)

            identity = await ensure_identity(claude_cli, settings)
            persona_template = (
                Path(__file__).parent.parent / "prompts" / "persona.txt"
            ).read_text()
            claude_cli.persona = render_persona(persona_template, identity)
            logger.info("I am %s %s", identity["name"], identity["emoji"])

            # Try z.ai first with OpenRouter fallback
            from .zai_cli import ZaiCLI

            llm_cli = ZaiCLI.from_settings(settings)
            if llm_cli is None:
                # Fall back to pure OpenRouter if z.ai is not configured
                if not settings.openrouter_api_key:
                    raise RuntimeError(
                        "Neither z.ai (via ~/.claude.json) nor OPENROUTER_API_KEY is configured"
                    )
                from .openrouter_cli import OpenRouterCLI

                llm_cli = OpenRouterCLI(
                    api_key=settings.openrouter_api_key,
                    model=settings.openrouter_model,
                    timeout=settings.openrouter_timeout_seconds,
                )
            else:
                logger.info("Using z.ai for generator with OpenRouter fallback")

            # Phase 2.2: ConversationManager is the source-of-truth for
            # conversation memory + action-log. Instantiated when the flag
            # is enabled OR (default) when OPENROUTER_API_KEY is configured
            # and the flag hasn't been explicitly disabled in config.toml.
            # Users who set conversation_memory_enabled=False get no memory.
            conversation_manager = None
            if settings.conversation_memory_enabled:
                from .conversation import ConversationManager
                topic_extractor = None
                if settings.topic_extraction_backend == "openrouter":
                    if settings.openrouter_api_key:
                        from .openrouter_topic_extractor import (
                            OpenRouterTopicExtractorCLI,
                        )
                        topic_extractor = OpenRouterTopicExtractorCLI(
                            api_key=settings.openrouter_api_key,
                            model=settings.topic_extraction_openrouter_model,
                            timeout=settings.topic_extraction_openrouter_timeout_seconds,
                        )
                        logger.info(
                            "Topic extraction backend: openrouter (model=%s)",
                            settings.topic_extraction_openrouter_model,
                        )
                    else:
                        logger.warning(
                            "topic_extraction_backend=openrouter but openrouter_api_key "
                            "is not set — falling back to Claude path"
                        )
                conversation_manager = ConversationManager(
                    store, claude_cli, topic_extractor=topic_extractor
                )
                # CCS-01: rebuild the in-memory action log from SQLite,
                # filtered to a freshness window so stale entries from
                # earlier sessions don't pollute the generator prompt.
                # Awaited (not fire-and-forget) so the deque is populated
                # before the first generator turn.
                try:
                    await conversation_manager.hydrate_action_log(
                        since_ts=time.time() - settings.conversation_idle_seconds,
                    )
                except Exception:
                    logger.exception(
                        "action_log hydrate failed (non-fatal) — starting empty"
                    )

            # Built without a gallery for now; if speaker_id_enabled loads
            # one below, we attach it in-place so recent transcripts carry
            # human-readable speaker labels.
            context_builder = ContextBuilder(store, settings, conversation_manager)

            from .actions import ActionWorker, Intent, IntentQueue

            intent_queue = IntentQueue(max_pending=settings.intent_queue_max_pending)
            # CCS-05a: bind the conversation_manager so cancel_active() can
            # mark drained intents as cancelled in the action log. Story 5b
            # will additionally bind a worker cancel_in_flight callback.
            if conversation_manager is not None:
                intent_queue.bind_conversation_manager(conversation_manager)

            # Acoustic diarization + LLM identity-inference subsystem. All
            # gates are conservative: any missing piece leaves the
            # subsystem silent and the pipeline shape unchanged.
            speaker_gallery = None
            speaker_model = None
            speaker_namer = None
            if settings.speaker_id_enabled:
                try:
                    from . import speaker_id as _sid_mod
                    from .speaker_gallery import SpeakerGallery as _Gallery

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
                from .speaker_namer import maybe_build_namer

                # Wire the loaded gallery into ContextBuilder so recent
                # transcripts surface human-readable labels (owner / Alice /
                # guest_02) instead of raw speaker ids.
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

            pipeline, processor, tts_cache, indication = await build_pipeline(
                settings,
                claude_cli,
                store,
                context_builder,
                llm_cli,
                persona=claude_cli.persona or "",
                intent_queue=intent_queue,
                conversation_manager=conversation_manager,
                speaker_gallery=speaker_gallery,
                speaker_model=speaker_model,
                namer_enqueue=namer_enqueue,
            )

            # Phase A (D3): callbacks speak action outcomes so the user hears
            # them instead of having to tail logs. Processor must exist first.
            from pipecat.frames.frames import TTSSpeakFrame  # noqa: E402
            from .generator import _scrub_tts_text

            async def _on_action_result(intent: "Intent", summary: str, result: dict) -> None:
                # Phase 2.2: record BEFORE logging so next turn's context
                # already reflects the completed action.
                if conversation_manager is not None:
                    # CCS-02: thread structured items (e.g. web_search hits)
                    # into the action log so the next-turn context renders
                    # numbered entries the LLM can address by ordinal.
                    items = result.get("items") if isinstance(result, dict) else None
                    conversation_manager.record_action_result(
                        intent.id, summary, items=items
                    )
                # Determine success/failure from result dict so direct-tool
                # failures (success=False with spoken key) are logged correctly
                # and do not fall through as "ok".
                is_success = result.get("success", True) is not False
                status_label = "ok" if is_success else "error"
                logger.info(
                    "[ACTION RESULT id=%d status=%s] summary=%s",
                    intent.id,
                    status_label,
                    summary[:200],
                )
                from .indication import IndicationKind, get_indication

                ind = get_indication()
                if ind is not None:
                    ind.notify(
                        IndicationKind.INTENT_COMPLETED, body=f"done: {intent.tool}"
                    )
                fallback = _spoken_action_summary(summary, _scrub_tts_text)
                spoken = _resolve_spoken(result, intent, fallback_summary=fallback)
                try:
                    await processor.push_frame(TTSSpeakFrame(spoken))
                    logger.info("[ACTION SPEAK id=%d len=%d]", intent.id, len(spoken))
                except Exception:
                    logger.exception("action result TTS push failed (non-fatal)")
                await _persist_action_outcome(store, intent, status_label, spoken)

            async def _on_action_error(intent: "Intent", exc: BaseException) -> None:
                if conversation_manager is not None:
                    conversation_manager.record_action_error(intent.id, repr(exc))
                logger.info("[ACTION ERROR id=%d] exc=%r", intent.id, exc)
                from .indication import IndicationKind, get_indication

                ind = get_indication()
                if ind is not None:
                    ind.notify(
                        IndicationKind.ACTION_FAILED,
                        body=f"{intent.tool}: {exc.__class__.__name__}",
                    )
                # Use generic per-language failure sentence for pure Python exceptions
                # (timeout, cancelled, etc.). No result dict is available here.
                hint = _resolve_spoken(
                    {"success": False},
                    intent,
                    fallback_summary=_action_error_hint(exc),
                )
                kind = (
                    "timeout"
                    if isinstance(exc, asyncio.TimeoutError)
                    else exc.__class__.__name__
                )
                try:
                    await processor.push_frame(TTSSpeakFrame(hint))
                    logger.info("[ACTION ERROR SPEAK id=%d kind=%s]", intent.id, kind)
                except Exception:
                    logger.exception("action error TTS push failed (non-fatal)")
                status = (
                    "cancelled"
                    if isinstance(exc, (asyncio.TimeoutError, asyncio.CancelledError))
                    else "error"
                )
                await _persist_action_outcome(store, intent, status, hint)

            # CCS-05b: thread the TTS service's cancel_pending hook through
            # the worker so cancel_in_flight can drop queued/in-flight TTS
            # frames with a 50ms fade-out. ``processor`` here is the
            # generator; the actual TTS service is exposed through the
            # pipeline but conveniently accessible via the same module.
            from .pipeline import build_pipeline as _bp  # noqa: F401 — type hint
            tts_cancel_hook = None
            try:
                # The pipeline returned (task, generator, tts_cache, indication)
                # — but `tts` is the EdgeTTSService instance, exposed via the
                # generator's tts_service attribute.
                _tts = getattr(processor, "_tts_service", None) or getattr(
                    processor, "tts_service", None
                )
                if _tts is not None and hasattr(_tts, "cancel_pending"):
                    tts_cancel_hook = _tts.cancel_pending
            except Exception:  # noqa: BLE001 — best-effort wiring
                logger.exception("TTS cancel-hook wiring failed (non-fatal)")

            action_worker = ActionWorker(
                queue=intent_queue,
                claude_cli=claude_cli,
                on_result=_on_action_result,
                on_error=_on_action_error,
                timeout=settings.action_timeout_seconds,
                tts_cancel=tts_cancel_hook,
            )
            # Defence-in-depth: ActionWorker.__init__ already calls
            # bind_worker, but state this contract explicitly so future
            # edits can't silently break the cancel hook.
            intent_queue.bind_worker(action_worker.cancel_in_flight)

            # Warm up the TTS cache with FIXED_PHRASES so cancel/confirm/etc. play
            # instantly. Failures are non-fatal — falls back to live TTS.
            from .tts_edge import synthesize_to_pcm
            from .tts_phrases import FIXED_PHRASES

            try:
                await tts_cache.warmup(
                    FIXED_PHRASES,
                    lambda text: synthesize_to_pcm(
                        text, settings.tts_voice, settings.tts_sample_rate
                    ),
                )
            except Exception as e:
                logger.warning("TTS cache warmup failed (non-fatal): %s", e)

            # Startup greeting is deferred: pushing a TTSSpeakFrame before the
            # pipeline runner has processed StartFrame drops the frame. We
            # schedule it as a task that waits ~1s after runner starts.
            async def _push_greeting() -> None:
                await asyncio.sleep(1.0)
                # DAEMON_STARTED fires AFTER StartFrame propagates and audio
                # init succeeds (we are 1s past pipeline start at this point).
                from .indication import IndicationKind, get_indication

                _ind = get_indication()
                if _ind is not None:
                    _ind.notify(
                        IndicationKind.DAEMON_STARTED,
                        body=f"{identity.get('name') or settings.wake_word} ready",
                    )
                # Use the persona's name (from identity.json) rather than the
                # wake word — wake word is the command trigger, name is the
                # bot's identity. They're allowed to differ.
                greeting_name = identity.get("name") or settings.wake_word
                _greetings = {"en": "online", "uk": "на зв'язку", "ru": "на связи"}
                _greeting_suffix = _greetings.get(settings.groq_language, "online")
                greeting = f"{greeting_name} {_greeting_suffix}"
                try:
                    await processor.push_frame(TTSSpeakFrame(greeting))
                except Exception:
                    logger.exception("startup greeting push failed (non-fatal)")

            # Startup ordering (US-P2.1-06): worker task starts BEFORE the
            # deferred greeting so any intent a too-eager generator submits
            # during boot has a live queue consumer.
            worker_task = asyncio.create_task(action_worker.run())
            namer_task = None
            if speaker_namer is not None:
                namer_task = asyncio.create_task(speaker_namer.run())
            asyncio.create_task(_push_greeting())

            heartbeat = HeartbeatTask(processor, settings.heartbeat_interval_minutes)
            warmup = WarmupTask(
                voice=settings.tts_voice,
                interval_seconds=settings.warmup_interval_seconds,
            )

            from pipecat.pipeline.runner import PipelineRunner  # noqa: E402

            runner = PipelineRunner()
            await run_until_stopped(
                runner, pipeline, heartbeat, warmup,
                decider=processor, worker_task=worker_task,
                namer_task=namer_task,
            )
    finally:
        ind = locals().get("indication")
        if ind is not None:
            try:
                from .indication import IndicationKind

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
    runner, pipeline, heartbeat, warmup=None, *, decider=None, worker_task=None,
    namer_task=None,
) -> None:
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

    # SIGHUP — re-read settings and reload the indication subsystem without
    # restart. Looks up the facade via the module-level singleton set by
    # pipeline.py:build_pipeline (no closure capture from serve()).
    def _handle_sighup() -> None:
        logger.info("received SIGHUP — reloading indication settings")
        from .config import load_settings
        from .indication import get_indication

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
    watch_set = {pipeline_task, heartbeat_task, stop_waiter}
    if warmup_task is not None:
        watch_set.add(warmup_task)
    if worker_task is not None:
        watch_set.add(worker_task)
    if namer_task is not None:
        watch_set.add(namer_task)
    try:
        done, _ = await asyncio.wait(watch_set, return_when=asyncio.FIRST_COMPLETED)
    finally:
        heartbeat.stop()
        if warmup is not None:
            warmup.stop()
        background_tasks = [pipeline_task, heartbeat_task, stop_waiter]
        if warmup_task is not None:
            background_tasks.append(warmup_task)
        if worker_task is not None:
            background_tasks.append(worker_task)
        if namer_task is not None:
            background_tasks.append(namer_task)
        for task in background_tasks:
            if not task.done():
                task.cancel()
        named_tasks = [(pipeline_task, "pipeline"), (heartbeat_task, "heartbeat")]
        if warmup_task is not None:
            named_tasks.append((warmup_task, "warmup"))
        if worker_task is not None:
            named_tasks.append((worker_task, "action-worker"))
        if namer_task is not None:
            named_tasks.append((namer_task, "speaker-namer"))
        for task, name in named_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("%s task crashed during shutdown", name)
        if decider is not None:
            try:
                await decider.shutdown()
            except Exception:
                logger.exception("decider shutdown failed")
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
    from .speaker_gallery import SpeakerGallery

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

    from .speaker_gallery import SpeakerGallery

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
    from .speaker_gallery import SpeakerGallery

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
    from .speaker_gallery import LabelValidationError, SpeakerGallery

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
    from .speaker_gallery import SpeakerGallery

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

    set_word_p = sub.add_parser("set-passphrase", help="Set the confirmation passphrase (restart required)")
    set_word_p.add_argument("word", help="Secret word to confirm actions (e.g. авторизую)")

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
    if cmd == "reset-session":
        return _cmd_reset_session(args)
    if cmd == "reset-identity":
        return _cmd_reset_identity(args)
    if cmd == "set-passphrase":
        return _cmd_set_wake_word(args)
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
