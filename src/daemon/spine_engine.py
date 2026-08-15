"""Run the spine as the daemon's engine — Phase E of the migration.

The dashboard, menubar and API are engine-agnostic: they speak to the
daemon through State, the voice_state file, the inject drop-folder and
the shared SQLite DB. This runner speaks all four dialects on the
spine's behalf, so `engine = "spine"` in config.toml swaps the whole
voice pipeline without touching a single line of frontend or API code.
Rollback is the same line saying "pipecat" again.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

logger = logging.getLogger("heare.spine_engine")

INJECT_POLL_SECS = 0.25
ROLE_POLL_SECS = 0.5
# Fast enough that a mute button feels instant, cheap enough to ignore:
# it reads an in-memory dict.
CONTROL_POLL_SECS = 0.2


async def run_spine_daemon(
    settings: Any,
    state: Any,
    api: Any,
    *,
    handle_signals: bool = True,
) -> None:
    """Boot and run the spine engine with the daemon's shell around it."""
    from src.pipeline.stages.voice_state_observer import write_voice_state
    from src.spine.audio_io import AudioIO
    from src.spine.main import _build_loop, _close_loop

    await state.init()
    api.state = state

    audio = AudioIO()
    await audio.start()
    loop = await _build_loop(
        settings,
        audio=audio,
        voice="",
        hold_s=float(getattr(settings, "spine_turn_hold_seconds", 1.3)),
        full=True,
    )

    # -- State / voice_state bridge -----------------------------------

    vs_path = settings.voice_state_file

    def _vs(phase: str, final: str | None = None) -> None:
        try:
            write_voice_state(vs_path, phase, last_final=final)
            state.set_cache_only(
                "voice_state", f'{{"state": "{phase}"}}'
            )
        except Exception:
            logger.debug("voice_state write failed (non-fatal)")

    real_stt = loop.transcribe

    async def _stt_with_state(pcm: bytes):
        _vs("stt")
        try:
            result = await real_stt(pcm)
        except Exception:
            _vs("listening")
            raise
        _vs("result", final=getattr(result, "text", None))
        return result

    loop.transcribe = _stt_with_state

    # -- role platform bridge: State keys the dashboard reads ----------

    available = [
        {
            "name": r.name,
            "channel": getattr(r, "channel", "voice"),
            "trigger": (r.triggers[0] if getattr(r, "triggers", ()) else r.name),
        }
        for r in getattr(loop, "roles", {}).values()
    ]
    state.set_cache_only(
        "roles_available", json.dumps(available, ensure_ascii=False)
    )

    async def _hint_sink(text: str, question: str = "") -> None:
        state.set_cache_only(
            "role_hint",
            json.dumps(
                {"ts": time.time(), "text": text, "question": question},
                ensure_ascii=False,
            ),
        )

    loop.hint_sink = _hint_sink

    async def _poll_role() -> None:
        last: tuple | None = None
        while True:
            await asyncio.sleep(ROLE_POLL_SECS)
            try:
                active = (
                    loop.role_manager.active if loop.role_manager else None
                )
                name = getattr(active, "name", "") if active else ""
                log = getattr(loop, "_role_log", [])
                turns = len(log) if active else 0
                last_heard = (log[-1]["user"][:100] if active and log else "")
                finishing = "1" if getattr(loop, "role_finishing", False) else ""
                snapshot = (name, turns, last_heard, finishing)
                if snapshot != last:
                    changed_role = last is None or snapshot[0] != last[0]
                    last = snapshot
                    state.set_cache_only("role_active", name)
                    state.set_cache_only("role_turns", str(turns))
                    state.set_cache_only("role_last_heard", last_heard)
                    state.set_cache_only("role_finishing", finishing)
                    if changed_role:
                        state.set_cache_only(
                            "role_channel",
                            getattr(active, "channel", "") if active else "",
                        )
                        state.set_cache_only(
                            "role_since", str(time.time()) if active else ""
                        )
                        if not active:
                            state.set_cache_only("role_hint", "")
            except Exception:
                logger.exception("role state poll failed (non-fatal)")

    # -- control poller: the dashboard's switches must reach the audio --

    async def _poll_controls() -> None:
        """POST /mute and POST /cancel only write State; nothing in the
        spine read it, so the dashboard's mic button was decoration."""
        last: tuple | None = None
        while True:
            await asyncio.sleep(CONTROL_POLL_SECS)
            try:
                mic = state.get_bool("mute_mic")
                bot = state.get_bool("mute_bot")
                if (mic, bot) != last:
                    last = (mic, bot)
                    audio.mute_input_user = mic
                    audio.mute_output_user = bot
                    if bot:
                        audio.stop_playback()
                    logger.info("controls: mic_muted=%s bot_muted=%s", mic, bot)
                if state.get("cancel") == "1":
                    await state.set("cancel", "0")
                    dropped = audio.stop_playback()
                    loop._interrupted = True
                    if loop.toolbox is not None:
                        loop.toolbox.cancel_all()
                    logger.info("controls: cancel — dropped %d bytes", dropped)
            except Exception:
                logger.exception("control poll failed (non-fatal)")

    # -- inject drop-folder poller (what the API's /inject writes) -----

    async def _poll_inject() -> None:
        inject_dir = settings.inject_dir
        inject_dir.mkdir(parents=True, exist_ok=True)
        while True:
            await asyncio.sleep(INJECT_POLL_SECS)
            try:
                for f in sorted(inject_dir.glob("*.txt")):
                    text = f.read_text("utf-8").strip()
                    f.unlink(missing_ok=True)
                    if text:
                        logger.info("inject: %r", text[:60])
                        await loop.inject(text)
            except Exception:
                logger.exception("inject poll failed (non-fatal)")

    # -- lifecycle -----------------------------------------------------

    stop = asyncio.Event()
    if handle_signals:
        import signal

        loop_ = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop_.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):
                pass

    await state.set("running", "true")
    _vs("listening")
    duplex = "full duplex" if loop._duplex else "half duplex"
    logger.info("spine engine up (%s, wake=%s)", duplex, loop.wake is not None)

    poller = asyncio.create_task(_poll_inject(), name="spine-inject-poll")
    role_poller = asyncio.create_task(_poll_role(), name="spine-role-poll")
    ctl_poller = asyncio.create_task(_poll_controls(), name="spine-ctl-poll")
    runner = asyncio.create_task(loop.run(), name="spine-run")
    try:
        waiter = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait(
            {runner, waiter}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in done:
            if t is runner and (exc := t.exception()) is not None:
                raise exc
    finally:
        for t in (poller, role_poller, ctl_poller, runner):
            t.cancel()
        await asyncio.gather(
            poller, role_poller, ctl_poller, runner, return_exceptions=True
        )
        await audio.stop()
        await _close_loop(loop)
        try:
            await state.set("running", "false")
            _vs("idle")
        except Exception:
            pass
        logger.info("spine engine down")
