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
import logging
from typing import Any

logger = logging.getLogger("heare.spine_engine")

INJECT_POLL_SECS = 0.25


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
        settings, audio=audio, voice="", hold_s=1.0, full=True
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
        for t in (poller, runner):
            t.cancel()
        await asyncio.gather(poller, runner, return_exceptions=True)
        await audio.stop()
        await _close_loop(loop)
        try:
            await state.set("running", "false")
            _vs("idle")
        except Exception:
            pass
        logger.info("spine engine down")
