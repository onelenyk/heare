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
# The dashboard sliders send 0.0–5.0; anything outside is a typo or a
# stale key, and a gain of 40 would be a wall of clipping.
GAIN_MIN = 0.0
GAIN_MAX = 5.0


def _clean_gain(raw: str, fallback: float = 1.0) -> float:
    """Parse a State gain/volume string; never raise, never go wild."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return fallback
    if value != value:  # NaN
        return fallback
    return max(GAIN_MIN, min(GAIN_MAX, value))


def _resolve_device(settings: Any, kind: str) -> int | None:
    """Resolve the device the API's picker wrote to a sounddevice index.

    The picker writes a device *name* into ``~/.heare/audio_input_device``
    / ``audio_output_device``; config.load reads those into
    ``settings.audio_{input,output}_device``. Returns None when nothing is
    chosen, the name no longer matches a device, or sounddevice cannot be
    queried — None means "system default", which is what the spine did
    before this existed.
    """
    name = getattr(settings, f"audio_{kind}_device", None)
    if not name:
        path = getattr(settings, f"audio_{kind}_device_file", None)
        try:
            if path is not None and path.exists():
                name = path.read_text("utf-8").strip()
        except Exception:
            name = None
    if not name:
        return None

    try:
        import sounddevice

        devices = sounddevice.query_devices()
        channels = "max_input_channels" if kind == "input" else "max_output_channels"
        wanted = name.lower()
        for index, dev in enumerate(devices):
            if wanted in dev["name"].lower() and int(dev.get(channels, 0)) > 0:
                logger.info("audio %s device: %r -> index %d", kind, name, index)
                return index
        logger.warning("audio %s device %r not found; using default", kind, name)
    except Exception:
        logger.exception("audio %s device lookup failed (using default)", kind)
    return None


async def _recover_role_session(persist: Any, state: Any) -> dict | None:
    """A meeting that a restart cut off must be reported, not resumed.

    A role session lives in RoleManager's memory; the daemon dying mid-
    meeting used to end it in silence — its turns stayed in the DB as
    orphans and no artifact was ever built. Now the row survives, so at
    boot we can find it. We deliberately do NOT resume it: the room has
    moved on, the microphone was off for however long the restart took,
    and a session silently continuing an hour later would record the
    wrong meeting. Instead the row is closed as 'interrupted' (so it is
    found exactly once) and the fact is put where the user can see it —
    State key `role_interrupted`, which the dashboard reads. Building the
    artifact from those turns is a later, deliberate step.

    Returns the recovered row, or None when there was nothing to recover.
    """
    if persist is None:
        return None
    try:
        row = await asyncio.to_thread(persist.live_role_session)
    except Exception:
        logger.exception("role session recovery: read failed (non-fatal)")
        return None
    if not row:
        return None

    turns = len(row.get("turn_ids") or [])
    role_name = row.get("role_name") or ""
    try:
        await asyncio.to_thread(
            persist.close_role_session, int(row["id"]), None, "interrupted"
        )
    except Exception:
        logger.exception("role session recovery: close failed (non-fatal)")
    logger.warning(
        "role session interrupted by restart: role=%r turns=%d (session %s)",
        role_name,
        turns,
        row.get("id"),
    )
    try:
        state.set_cache_only(
            "role_interrupted",
            json.dumps(
                {"role": role_name, "turns": turns, "ts": time.time()},
                ensure_ascii=False,
            ),
        )
    except Exception:
        logger.debug("role_interrupted state write failed (non-fatal)")
    return row


async def run_spine_daemon(
    settings: Any,
    state: Any,
    api: Any,
    *,
    handle_signals: bool = True,
) -> None:
    """Boot and run the spine engine with the daemon's shell around it."""
    from src.spine.audio_io import AudioIO
    from src.spine.main import _build_loop, _close_loop
    from src.spine.telemetry import Telemetry
    from src.spine.voice_state import write_voice_state

    await state.init()
    api.state = state

    # The device pickers write a name to a file; resolve it once, here,
    # because a stream's device cannot be changed after it is open.
    audio = AudioIO(
        input_device=_resolve_device(settings, "input"),
        output_device=_resolve_device(settings, "output"),
    )
    audio.input_gain = _clean_gain(state.get("input_gain"))
    audio.output_volume = _clean_gain(state.get("output_volume"))
    await audio.start()
    loop = await _build_loop(
        settings,
        audio=audio,
        voice="",
        hold_s=float(getattr(settings, "spine_turn_hold_seconds", 1.3)),
        full=True,
    )

    # The memories card asks the API, and the API needs the backend the
    # spine already opened — it was only ever set on the pipecat path.
    api._memory_backend = getattr(loop, "memory", None)

    # -- MCP: the servers in .mcp.json, made callable by the worker -----
    #
    # connect_mcp_servers never raises and skips whatever will not start,
    # so a broken entry costs those tools and nothing else. The bridge is
    # hung on the loop because the worker looks it up at call time and
    # _make_prompt reads it once per turn.
    loop.mcp = None
    try:
        from src.agent.mcp_bridge import connect_mcp_servers

        bridge = await connect_mcp_servers(settings)
        loop.mcp = bridge
        names = bridge.register_worker_tools()
        closers = getattr(loop, "_closers", None)
        if closers is not None:
            closers.append(bridge.aclose)
        logger.info(
            "mcp: %d server(s) connected (%s), %d tool(s) for the worker",
            len(bridge.connected_servers),
            ", ".join(bridge.connected_servers) or "none",
            len(names),
        )
    except Exception:
        # Only a bug in the wiring above can land here — connect itself
        # is already fail-soft — and even then the assistant must come up.
        logger.exception("mcp: bridge unavailable (non-fatal)")

    # -- telemetry: one JSONL line per turn -----------------------------
    # Cheap, stdlib-only, never raises into the conversation. See
    # docs/findings/measuring.md — "it feels slow" used to only be
    # answerable by ear; this is the instrument for that question.

    telemetry = Telemetry(settings.log_dir / "turns.jsonl")

    def _active_role_name() -> str:
        active = loop.role_manager.active if loop.role_manager else None
        return getattr(active, "name", "") if active else ""

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

    def _agent(phase: str) -> None:
        """idle | talking | interrupted — what the dashboard's AGENT bar
        reads. Nothing wrote this key on the spine, so the bar showed a
        permanently idle assistant even mid-sentence."""
        try:
            state.set_cache_only(
                "agent_state",
                json.dumps({"state": phase, "since_ts": time.time()}),
            )
        except Exception:
            logger.debug("agent_state write failed (non-fatal)")

    _agent("idle")

    real_stt = loop.transcribe

    async def _stt_with_state(pcm: bytes):
        _vs("stt")
        t0 = time.monotonic()
        try:
            result = await real_stt(pcm)
        except Exception:
            _vs("listening")
            raise
        stt_ms = int((time.monotonic() - t0) * 1000)
        text = (getattr(result, "text", None) or "").strip()
        dropped = not text
        telemetry.stt(stt_ms, dropped=dropped)
        if dropped:
            # An empty transcript never becomes a turn — nothing will
            # call respond() to close this line out, so write it now or
            # it is lost. No think/speak timings: there is no turn.
            telemetry.finish(chars=0, interrupted=False, role=_active_role_name())
        _vs("result", final=getattr(result, "text", None))
        # Back to listening: without this the ear stayed on "result"
        # until the next utterance, so the bar never rested.
        _vs("listening")
        return result

    loop.transcribe = _stt_with_state

    # First LLM delta of the turn — whichever streaming path is wired.
    # Both also run for hint generation / role-summary calls that are
    # not part of the normal turn; first_delta() is a harmless no-op
    # then because no turn_closed() opened a window to land in.

    if loop.stream_chat is not None:
        real_stream_chat = loop.stream_chat

        def _stream_chat_with_telemetry(messages):
            async def _gen():
                first = True
                async for delta in real_stream_chat(messages):
                    if first:
                        telemetry.first_delta()
                        first = False
                    yield delta

            return _gen()

        loop.stream_chat = _stream_chat_with_telemetry

    if loop.stream_events is not None:
        real_stream_events = loop.stream_events

        def _stream_events_with_telemetry(messages, tools):
            async def _gen():
                first = True
                async for ev in real_stream_events(messages, tools):
                    if first and ev.get("type") == "delta":
                        telemetry.first_delta()
                        first = False
                    yield ev

            return _gen()

        loop.stream_events = _stream_events_with_telemetry

    real_speak = loop._speak

    async def _speak_with_state(sentence: str):
        _agent("talking")
        # First call per turn wins (first_audio() no-ops after that) —
        # the earliest hook available to this wrapper for "audio started
        # playing" without touching loop.py's synthesise loop directly.
        telemetry.first_audio()
        try:
            return await real_speak(sentence)
        finally:
            _agent("interrupted" if loop._interrupted else "idle")

    loop._speak = _speak_with_state

    real_respond = loop.respond

    async def _respond_with_telemetry(user_text: str, *, speak: bool = True) -> str:
        telemetry.turn_closed()
        reply = ""
        try:
            reply = await real_respond(user_text, speak=speak)
            return reply
        finally:
            telemetry.finish(
                chars=len(reply),
                interrupted=loop._interrupted,
                role=_active_role_name(),
            )

    loop.respond = _respond_with_telemetry

    # -- role platform bridge: State keys the dashboard reads ----------

    # Before anything can open a new session, deal with the one the last
    # run left open (if any) — see _recover_role_session.
    state.set_cache_only("role_interrupted", "")
    await _recover_role_session(getattr(loop, "persist", None), state)

    # The DB mirror of the live session. main.py builds the RoleManager
    # before the SpinePersistence handle exists, so the collaborator is
    # attached here; without it the manager behaves exactly as before.
    if getattr(loop, "role_manager", None) is not None:
        loop.role_manager.persist = getattr(loop, "persist", None)

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
        spine read it, so the dashboard's mic button was decoration.

        The two sliders (State `input_gain`, `output_volume`) were dead
        the same way: their only reader was a pipecat stage."""
        last: tuple | None = None
        # Seeded with what start-up already applied, so a fresh daemon
        # does not log a change that did not happen.
        last_levels: tuple[float, float] = (audio.input_gain, audio.output_volume)
        while True:
            await asyncio.sleep(CONTROL_POLL_SECS)
            try:
                mic = state.get_bool("mute_mic")
                bot = state.get_bool("mute_bot")
                no_interrupt = state.get_bool("interrupt_off")
                if (mic, bot, no_interrupt) != last:
                    last = (mic, bot, no_interrupt)
                    audio.mute_input_user = mic
                    audio.mute_output_user = bot
                    loop.barge_in_enabled = not no_interrupt
                    if bot:
                        audio.stop_playback()
                    logger.info(
                        "controls: mic_muted=%s bot_muted=%s barge_in=%s",
                        mic,
                        bot,
                        not no_interrupt,
                    )
                levels = (
                    _clean_gain(state.get("input_gain")),
                    _clean_gain(state.get("output_volume")),
                )
                if levels != last_levels:
                    last_levels = levels
                    audio.input_gain, audio.output_volume = levels
                    logger.info(
                        "controls: input_gain=%.2f output_volume=%.2f",
                        levels[0],
                        levels[1],
                    )
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
