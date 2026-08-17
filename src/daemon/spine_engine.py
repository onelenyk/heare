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
from pathlib import Path
from typing import Any

logger = logging.getLogger("heare.spine_engine")

INJECT_POLL_SECS = 0.25
ROLE_POLL_SECS = 0.5
# Fast enough that a mute button feels instant, cheap enough to ignore:
# it reads an in-memory dict.
CONTROL_POLL_SECS = 0.2
# .mcp.json is edited by hand or by the installer, not at frame rate; one
# stat a second is invisible and still feels immediate.
MCP_POLL_SECS = 1.0
# How long the file must hold still before it counts as a finished edit.
MCP_SETTLE_SECS = 0.5
# The dashboard sliders send 0.0–5.0; anything outside is a typo or a
# stale key, and a gain of 40 would be a wall of clipping.
GAIN_MIN = 0.0
GAIN_MAX = 5.0
# How often a boot that is waiting for a key re-reads the settings. Same
# cadence as the legacy engine's wait loop in src/main.py.
BOOT_POLL_SECS = 1.0
# A moment after the loop starts running, so the greeting does not race
# the first audio callbacks.
GREETING_DELAY_SECS = 0.6


# -- keys the engine cannot work without -----------------------------
#
# The spine has exactly two: DeepSeek answers, Groq hears. Missing the
# first, _build_loop raises out of resolve_llm and the daemon used to die
# with a traceback; missing the second, it came up looking healthy and
# dropped every utterance in silence. Neither is a crash and neither is a
# secret — both are a fact for the dashboard and a thing to wait for.
REQUIRED_KEYS: tuple[tuple[str, str, str], ...] = (
    (
        "deepseek_api_key",
        "DEEPSEEK_API_KEY",
        "нема кому відповідати — модель недоступна",
    ),
    (
        "groq_api_key",
        "GROQ_API_KEY",
        "нічого не чує — розпізнавання мовлення недоступне",
    ),
)
KEY_HINT = (
    "Add it in the dashboard (Setup → Keys) or in ~/.heare/.env — "
    "the daemon picks it up within a second, no restart needed."
)


def missing_keys(settings: Any) -> list[str]:
    """Which required keys are not set on this settings object."""
    return [
        attr
        for attr, _env, _cost in REQUIRED_KEYS
        if not str(getattr(settings, attr, None) or "").strip()
    ]


def _env_names(attrs: list[str]) -> str:
    by_attr = {attr: env for attr, env, _cost in REQUIRED_KEYS}
    return ", ".join(by_attr.get(a, a) for a in attrs)


def _key_costs(attrs: list[str]) -> list[str]:
    by_attr = {attr: cost for attr, _env, cost in REQUIRED_KEYS}
    return [f"{a}: {by_attr[a]}" for a in attrs if a in by_attr]


def publish_boot_status(
    state: Any,
    *,
    ok: bool,
    phase: str,
    missing: list[str] | None = None,
    error: str = "",
    waited_s: float = 0.0,
    greeting: str = "",
) -> dict:
    """Say out loud where the boot got to — State key ``boot_status``.

    Cache-only, like mcp_status: it describes *this* process, and a value
    surviving into the next boot would describe a daemon that is gone.
    The dashboard renders it; without it a boot waiting for a key is
    indistinguishable from a boot that hung.
    """
    missing = list(missing or [])
    status = {
        "ok": bool(ok),
        "phase": phase,
        "missing": missing,
        "costs": _key_costs(missing),
        "error": error,
        "waited_s": round(float(waited_s), 1),
        "greeting": greeting,
        "hint": KEY_HINT if missing else "",
        "ts": time.time(),
    }
    if state is not None:
        try:
            state.set_cache_only(
                "boot_status", json.dumps(status, ensure_ascii=False)
            )
        except Exception:
            logger.debug("boot_status write failed (non-fatal)")
    return status


def _reload_settings(settings: Any) -> Any:
    """Re-read ``~/.heare/.env`` and config.toml, the way src/main.py's
    legacy wait loop does.

    A daemon started before the key existed holds a Settings that says it
    still does not; the dashboard's Save Keys writes the .env, not this
    process's memory. Without a reload the wait could never end. A failed
    reload is not fatal — keep the settings we have and try again.
    """
    try:
        from dotenv import load_dotenv
        from src.config import load_settings

        load_dotenv(Path.home() / ".heare" / ".env", override=True)
        fresh = load_settings()
        fresh.ensure_dirs()
        return fresh
    except Exception:
        logger.exception("boot: settings reload failed (keeping the old ones)")
        return settings


async def _stopped(stop: Any, secs: float) -> bool:
    """Sleep, unless the daemon is asked to stop first."""
    if stop is None:
        await asyncio.sleep(secs)
        return False
    try:
        await asyncio.wait_for(stop.wait(), timeout=secs)
        return True
    except asyncio.TimeoutError:
        return False


async def _wait_for_keys(settings: Any, state: Any, stop: Any) -> tuple[Any, bool]:
    """Block until every required key is set. Never raises, never exits.

    Returns ``(settings, True)`` once the keys are there — the settings
    are the *reloaded* ones, so the rest of boot reads the same file the
    user just edited. ``(settings, False)`` means the daemon was asked to
    stop while waiting.
    """
    missing = missing_keys(settings)
    if not missing:
        return settings, True

    logger.error(
        "boot: %s not set — the assistant cannot start. %s (%s)",
        _env_names(missing),
        KEY_HINT,
        "; ".join(_key_costs(missing)),
    )
    waited = 0.0
    while True:
        publish_boot_status(
            state,
            ok=False,
            phase="waiting_for_keys",
            missing=missing,
            waited_s=waited,
        )
        if await _stopped(stop, BOOT_POLL_SECS):
            publish_boot_status(
                state, ok=False, phase="stopped", missing=missing, waited_s=waited
            )
            return settings, False
        waited += BOOT_POLL_SECS
        settings = _reload_settings(settings)
        missing = missing_keys(settings)
        if not missing:
            logger.info("boot: keys found after %.0fs — starting", waited)
            publish_boot_status(state, ok=True, phase="starting", waited_s=waited)
            return settings, True


async def _release_audio(audio: Any) -> None:
    """Close the streams a failed boot opened. A dying process holding
    the microphone is the difference between a crash and a broken mic."""
    if audio is None:
        return
    try:
        await audio.stop()
    except Exception:
        logger.exception("boot: releasing the audio streams failed (non-fatal)")


def _greeting_enabled(settings: Any) -> bool:
    """On by default; off via config (``spine_startup_greeting = false``,
    once config.py grows the field) or ``HEARE_NO_GREETING=1`` today."""
    import os

    if os.environ.get("HEARE_NO_GREETING", "").strip() not in ("", "0", "false"):
        return False
    return bool(getattr(settings, "spine_startup_greeting", True))


def greeting_text(settings: Any) -> str:
    """Three words, in the language the assistant is spoken to in.

    Short on purpose: it exists to prove the speaker works and the daemon
    is up, and anything longer is something to talk over every restart.
    """
    name = str(getattr(settings, "wake_word", "") or "").strip()
    if name:
        return f"{name[:1].upper()}{name[1:]} на зв'язку."
    return "На зв'язку."


def _clean_gain(raw: str, fallback: float = 1.0) -> float:
    """Parse a State gain/volume string; never raise, never go wild."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return fallback
    if value != value:  # NaN
        return fallback
    return max(GAIN_MIN, min(GAIN_MAX, value))


# -- feature switches ------------------------------------------------
#
# src/spine/features.py promises that `off` means *not wired at all*.
# Seven subsystems are switched in the composition root (src/spine/main.py);
# the other two — the MCP bridge and telemetry — are built by this runner,
# so this is the only place their switch can bite.

# What to ask the live loop about, per feature name. An entry here makes
# the published answer OBSERVED: the object is there, or it is not.
# A feature with no entry can only be DECLARED — the resolved flag, which
# is what was asked for, not proof that it was built.
_LIVE_ATTR: dict[str, str] = {
    "aec": "aec",
    "wake": "wake",
    "tools": "toolbox",
    "roles": "role_flow",
    "mcp": "mcp",
    "memory": "memory",
    "persist": "persist",
    "usage": "usage",
    "engine": "engine",
}


def _feature(loop: Any, name: str) -> bool:
    """Is `name` switched on for this run?

    Reads the table the composition root resolved and hung on the loop
    (env > --without > config.toml > defaults). A loop without one — an
    older build, a test fake — falls back to the declared default, so a
    missing table can never silently disable a subsystem.
    """
    from src.spine.features import FEATURES

    table = getattr(loop, "features", None) or {}
    default = next((f.default for f in FEATURES if f.name == name), True)
    return bool(table.get(name, default))


class _NoTelemetry:
    """`--without telemetry`: the same shape, none of the effects.

    A null object rather than an `if telemetry is not None` at every call
    site. It opens no file and writes no line, so turns.jsonl stops
    existing instead of being written by a subsystem reported as off.
    """

    # How wired_features() tells a real instrument from this one.
    wired = False

    def stt(self, ms: int, *, dropped: bool) -> None:
        return None

    def turn_closed(self) -> None:
        return None

    def first_delta(self) -> None:
        return None

    def first_audio(self) -> None:
        return None

    def finish(self, *, chars: int, interrupted: bool, role: str) -> None:
        return None


def wired_features(loop: Any, telemetry: Any = None) -> list[dict]:
    """What is actually wired, for the dashboard — not what was asked for.

    The feature table exists to bisect a bad evening, so it has to report
    the loop that is running, not the config that described it: a
    subsystem that failed to build must read `off`, or the instrument is
    worse than useless.

    Each entry carries ``observed``:

      observed=True   the answer comes from the live object — the eight
                      names in ``_LIVE_ATTR`` (``loop.mcp is not None``,
                      ``loop.aec is not None``, …) plus ``telemetry``,
                      which is the object this runner built.
      observed=False  no object exists to ask, so the resolved flag is
                      repeated as-is. Nothing is in this class today; the
                      branch is here so a future feature without a live
                      object is labelled honestly instead of silently
                      passing itself off as a fact.

    One caveat, and it is deliberate: ``mcp`` connects off the boot path
    (a cold `npx` can take a minute), so it honestly reads `off` until the
    bridge lands — run_spine_daemon publishes again when it does.
    """
    from src.spine.features import FEATURES

    declared = getattr(loop, "features", None) or {}
    entries: list[dict] = []
    for f in FEATURES:
        if f.name == "telemetry":
            # A real Telemetry has no `wired` attribute; _NoTelemetry
            # sets it False, and None means nothing was built at all.
            on = bool(getattr(telemetry, "wired", telemetry is not None))
            observed = True
        elif f.name in _LIVE_ATTR:
            on = getattr(loop, _LIVE_ATTR[f.name], None) is not None
            observed = True
        else:
            on = bool(declared.get(f.name, f.default))
            observed = False
        entries.append(
            {"name": f.name, "on": bool(on), "cost": f.cost, "observed": observed}
        )
    return entries


def _publish_features(state: Any, loop: Any, telemetry: Any = None) -> None:
    """Put the wired truth where the dashboard's FeaturesCard reads it.

    Cache-only: it describes this process. Called again whenever a
    subsystem appears or fails to appear after boot (the MCP bridge).
    """
    if state is None:
        return
    try:
        state.set_cache_only(
            "spine_features",
            json.dumps(wired_features(loop, telemetry), ensure_ascii=False),
        )
    except Exception:
        logger.debug("spine_features state write failed (non-fatal)")


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


def _mcp_fingerprint(path: Path) -> tuple[int, int] | None:
    """(mtime_ns, size) of the MCP config, or None when it is not there."""
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


class _McpConfigWatcher:
    """Fires once per *settled* change to ``.mcp.json``.

    mtime+size rather than a content hash: it is one stat, and a config
    is not written at frame rate. The settle window is not politeness —
    an editor that truncates and then writes leaves the file empty for a
    moment, and a reload started there would read half a config. A
    fingerprint therefore has to hold still for ``settle_s`` before it
    counts, which also collapses two edits a second apart into one
    reload instead of two reconnects.
    """

    def __init__(
        self,
        path: Path,
        *,
        settle_s: float = MCP_SETTLE_SECS,
        clock: Any = time.monotonic,
    ) -> None:
        self._path = Path(path)
        self._settle_s = settle_s
        self._clock = clock
        # Seeded with what boot already loaded, so a daemon that starts
        # up does not immediately reload the config it just read.
        self._seen = _mcp_fingerprint(self._path)
        self._pending: tuple[int, int] | None = None
        self._pending_at = 0.0

    def poll(self) -> bool:
        fp = _mcp_fingerprint(self._path)
        if fp == self._seen:
            self._pending = None
            return False
        now = self._clock()
        if fp != self._pending:
            self._pending = fp
            self._pending_at = now
            return False
        if now - self._pending_at < self._settle_s:
            return False
        self._seen = fp
        self._pending = None
        return True

    def accept(self) -> None:
        """Treat the file as already handled (after a forced reload)."""
        self._seen = _mcp_fingerprint(self._path)
        self._pending = None


def _mcp_status(bridge: Any, *, ok: bool, error: str = "") -> dict:
    servers = list(getattr(bridge, "connected_servers", None) or [])
    tools = list(getattr(bridge, "tool_names", None) or [])
    return {
        "servers": servers,
        "tools": len(tools),
        "ok": ok,
        "error": error,
        "ts": time.time(),
    }


def _publish_mcp_status(state: Any, bridge: Any, *, ok: bool, error: str = "") -> dict:
    """Put what is connected where the dashboard can read it.

    Cache-only on purpose: it describes live subprocesses, and a value
    surviving into the next boot would describe servers that are not
    running.
    """
    status = _mcp_status(bridge, ok=ok, error=error)
    if state is not None:
        try:
            state.set_cache_only("mcp_status", json.dumps(status, ensure_ascii=False))
        except Exception:
            logger.debug("mcp_status write failed (non-fatal)")
    return status


async def _aclose_bridge(bridge: Any, *, unregister: bool) -> None:
    if bridge is None:
        return
    try:
        await bridge.aclose(unregister=unregister)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("mcp reload: closing a bridge failed (non-fatal)")


def _swap_closer(loop: Any, old: Any, new: Any) -> None:
    """Teardown must close the bridge that is alive, not the dead one.

    ``_closers`` holds a bound ``bridge.aclose``; left alone it would
    keep a reference to a bridge whose servers are already gone while
    the running ones were never closed at all — orphaned `npx`
    processes outliving the daemon.
    """
    closers = getattr(loop, "_closers", None)
    if closers is None:
        return
    if old is not None:
        for i, closer in enumerate(closers):
            if getattr(closer, "__self__", None) is old:
                del closers[i]
                break
    closers.append(new.aclose)


def _rebuild_capability_index(settings: Any) -> None:
    """Refresh the index *if* this process already built one.

    The spine never builds it at boot (only the pipecat path does, in
    src/pipeline/build.py); on this engine it appears lazily, the first
    time a capability tool runs. Building one here would be inventing
    work — and an index built later reads the new .mcp.json by itself.
    So: rebuild what exists, touch nothing otherwise.
    """
    try:
        from src.agent.tools import direct as direct_tools

        index = getattr(direct_tools, "_capability_index_singleton", None)
        if index is None:
            logger.debug("mcp reload: no capability index in this process yet")
            return
        index.rebuild()
        logger.info("mcp reload: capability index rebuilt")
    except Exception:
        logger.exception("mcp reload: capability index rebuild failed (non-fatal)")


async def reload_mcp(
    settings: Any,
    loop: Any,
    state: Any = None,
    *,
    connect: Any = None,
) -> dict:
    """Re-read ``.mcp.json`` and swap in a freshly connected bridge.

    The contract is that a bad config costs nothing: the new bridge is
    connected *first*, and only a set that is actually better than the
    one already running replaces it. Whatever happens, this returns the
    published status dict and never raises (except on cancellation).

    ``connect`` is the test seam — an async ``(settings) -> bridge``.
    """
    from src.agent.mcp_bridge import (
        connect_mcp_servers,
        enabled_servers,
        load_mcp_config,
        mcp_config_path,
    )

    connect = connect or connect_mcp_servers
    old = getattr(loop, "mcp", None)
    old_names = list(getattr(old, "tool_names", None) or [])

    path = mcp_config_path(settings)
    servers = load_mcp_config(path)
    if servers is None:
        logger.warning(
            "mcp reload: %s is missing or not valid JSON — keeping %d tool(s) "
            "from %s",
            path,
            len(old_names),
            ", ".join(getattr(old, "connected_servers", None) or []) or "nothing",
        )
        return _publish_mcp_status(state, old, ok=False, error="config unreadable")

    wanted = enabled_servers(servers)

    try:
        new = await connect(settings)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "mcp reload: connecting failed (%s) — keeping %d tool(s)",
            exc,
            len(old_names),
        )
        return _publish_mcp_status(state, old, ok=False, error=f"connect failed: {exc}")

    new_names = list(getattr(new, "tool_names", None) or [])
    if wanted and not new_names and old_names:
        # The config asks for servers and not one of them came up. The
        # old bridge is still live and still registered; ending here is
        # the whole point of connecting before swapping.
        logger.warning(
            "mcp reload: none of the %d configured server(s) started — keeping "
            "the previous %d tool(s)",
            len(wanted),
            len(old_names),
        )
        await _aclose_bridge(new, unregister=False)
        return _publish_mcp_status(state, old, ok=False, error="no server connected")

    # The swap. Nothing is awaited between taking the old names out of
    # the shared registry and putting the new ones in (both happen
    # inside register_worker_tools), so no job can see an empty toolset.
    new.register_worker_tools()
    loop.mcp = new
    _swap_closer(loop, old, new)
    await _aclose_bridge(old, unregister=False)

    _rebuild_capability_index(settings)
    logger.info(
        "mcp reload: %d server(s) connected (%s), %d tool(s) for the worker "
        "(was %d)",
        len(getattr(new, "connected_servers", None) or []),
        ", ".join(getattr(new, "connected_servers", None) or []) or "none",
        len(new_names),
        len(old_names),
    )
    return _publish_mcp_status(state, new, ok=True)


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

    # Installed before the boot, not after it: a daemon waiting for an
    # API key must still answer Ctrl-C and SIGTERM, and the wait below is
    # the one place that can last forever.
    stop = asyncio.Event()
    if handle_signals:
        import signal

        loop_ = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop_.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):
                pass

    # -- boot: wait for what is missing, and never leak what was opened -
    #
    # Two failures used to end the process here. A missing key raised out
    # of resolve_llm, so `heare start` died with a traceback and the
    # dashboard's ▶ did the same, every time — now it waits and says so.
    # And audio.start() ran outside any try, so a build that raised left
    # the microphone and speaker streams open in a dying process.
    publish_boot_status(state, ok=True, phase="starting")
    audio: Any = None
    loop: Any = None
    while True:
        settings, ready = await _wait_for_keys(settings, state, stop)
        if not ready:
            logger.info("spine engine: asked to stop while waiting for keys")
            try:
                await state.set("running", "false")
            except Exception:
                pass
            return

        # The device pickers write a name to a file; resolve it once,
        # here, because a stream's device cannot be changed after it is
        # open — and after the wait, so a device chosen while the key was
        # missing is the one that gets opened.
        audio = AudioIO(
            input_device=_resolve_device(settings, "input"),
            output_device=_resolve_device(settings, "output"),
        )
        audio.input_gain = _clean_gain(state.get("input_gain"))
        audio.output_volume = _clean_gain(state.get("output_volume"))
        try:
            await audio.start()
            loop = await _build_loop(
                settings,
                audio=audio,
                voice="",
                hold_s=float(getattr(settings, "spine_turn_hold_seconds", 1.3)),
                full=True,
            )
        except Exception as exc:
            await _release_audio(audio)
            audio = None
            still_missing = missing_keys(settings)
            if still_missing:
                # A key removed (or written half-way) between the check
                # and the build. Same answer as before: wait for it.
                logger.error(
                    "boot: build failed and %s is not set — waiting (%s)",
                    _env_names(still_missing),
                    exc,
                )
                publish_boot_status(
                    state,
                    ok=False,
                    phase="waiting_for_keys",
                    missing=still_missing,
                    error=f"{type(exc).__name__}: {exc}",
                )
                continue
            publish_boot_status(
                state,
                ok=False,
                phase="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            logger.exception("spine engine: boot failed — audio streams released")
            raise
        break

    # The memories card asks the API, and the API needs the backend the
    # spine already opened — it was only ever set on the pipecat path.
    api._memory_backend = getattr(loop, "memory", None)

    # -- telemetry: one JSONL line per turn -----------------------------
    # Cheap, stdlib-only, never raises into the conversation. See
    # docs/findings/measuring.md — "it feels slow" used to only be
    # answerable by ear; this is the instrument for that question.
    #
    # Switched off it is not built at all: a null object stands in at the
    # call sites, so no file is opened and turns.jsonl stops appearing.
    # Built before the MCP block only so the state publisher below has it.

    telemetry_on = _feature(loop, "telemetry")
    telemetry: Any = (
        Telemetry(settings.log_dir / "turns.jsonl")
        if telemetry_on
        else _NoTelemetry()
    )
    if not telemetry_on:
        logger.info("telemetry: OFF — no turns.jsonl this run")

    # Long delegated work has no voice of its own on this engine: the
    # indication facade lives on the old one. A State key is enough for
    # the dashboard to show that something is still running.
    loop.on_long_running = lambda label: state.set_cache_only(
        "hands_progress",
        json.dumps({"ts": time.time(), "label": label}, ensure_ascii=False),
    )

    # -- MCP: the servers in .mcp.json, made callable by the worker -----
    #
    # connect_mcp_servers never raises and skips whatever will not start,
    # so a broken entry costs those tools and nothing else. The bridge is
    # hung on the loop because the worker looks it up at call time and
    # _make_prompt reads it once per turn.
    loop.mcp = None
    mcp_on = _feature(loop, "mcp")

    async def _connect_mcp() -> None:
        """Off the boot path: `npx -y` on a cold cache can take a minute
        per server, and the assistant must be listening long before it
        knows about anyone's files. The tools appear when they appear —
        the worker rebuilds its schema list before every job, so nothing
        needs to be ready at startup."""
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
        _publish_mcp_status(state, bridge, ok=True)

    mcp_task: asyncio.Task | None = None
    if not mcp_on:
        # `off` means no subprocess is spawned, no tool is registered for
        # the worker, and no config watcher can bring either back — see
        # _poll_mcp below. The status key is written anyway so the
        # dashboard says "off" instead of showing the last boot's servers.
        logger.info("mcp: OFF — no servers, no external tools for the worker")
        _publish_mcp_status(state, None, ok=False, error="off")
    else:
        try:
            mcp_task = asyncio.create_task(_connect_mcp(), name="spine-mcp-connect")

            def _mcp_done(task: asyncio.Task) -> None:
                if not task.cancelled() and task.exception() is not None:
                    logger.warning("mcp: connect failed — %s", task.exception())
                    _publish_mcp_status(
                        state, None, ok=False, error=str(task.exception())
                    )
                # Whether it landed or not, the feature card must now show
                # what happened rather than what was asked for.
                _publish_features(state, loop, telemetry)

            mcp_task.add_done_callback(_mcp_done)
        except Exception:
            # Only a bug in the wiring above can land here — connect
            # itself is already fail-soft — and even then the assistant
            # must come up.
            logger.exception("mcp: bridge unavailable (non-fatal)")

    # -- what is actually wired, for the dashboard ----------------------
    #
    # Derived from the live loop, not from the config: env overrides and
    # --without win over it, and a subsystem can fail to build. See
    # wired_features() for which entries are observed and which declared.
    _publish_features(state, loop, telemetry)

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
    #
    # These two wrappers exist only to time the stream, so with telemetry
    # off they are not installed at all — the model's deltas travel the
    # path they travelled before telemetry existed.

    if telemetry_on and loop.stream_chat is not None:
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

    if telemetry_on and loop.stream_events is not None:
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

    # Purely an instrument, like the two stream wrappers: off means the
    # turn is not wrapped at all.
    if telemetry_on:
        real_respond = loop.respond

        async def _respond_with_telemetry(
            user_text: str, *, speak: bool = True
        ) -> str:
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

    # -- MCP config watcher: new servers without a restart -------------

    async def _poll_mcp() -> None:
        """Reload MCP servers when their config changes.

        Its own poller rather than a branch in ``_poll_controls``: a
        reload awaits a subprocess spawn per server — up to a minute each
        on the bridge's own timeout — and the control poller is what
        carries the cancel button, the mute switch and the sliders.
        Sharing one coroutine would freeze all of those for the length of
        an `npx` cold start. Once a second is plenty for a file a person
        edits, and it is one stat.

        Two ways in, both free: the file changing (which is also how the
        installer and `revoke_capability` announce themselves — they
        write .mcp.json), and State key ``mcp_reload`` = "1", which the
        existing generic ``POST /state`` endpoint can set from the
        dashboard, a curl, or the agent's own bash tool. Same shape as
        the ``cancel`` key above.

        With ``mcp`` switched off this poller is never started: a config
        edit (or a stray ``mcp_reload``) reconnecting the servers would
        undo the switch a minute after boot, which is exactly the kind of
        lie the feature table exists to prevent.
        """
        from src.agent.mcp_bridge import mcp_config_path

        cfg_path = mcp_config_path(settings)
        watcher = _McpConfigWatcher(cfg_path)
        # ``mcp_reload`` is persisted, so a request made just before the
        # last run died would be honoured now — a pointless reconnect of
        # servers this boot has only just started. Clear it first.
        if state.get("mcp_reload") == "1":
            await state.set("mcp_reload", "0")
        while True:
            await asyncio.sleep(MCP_POLL_SECS)
            try:
                # The boot connect owns loop.mcp until it finishes; a
                # reload racing it would register two bridges over each
                # other. Nothing is polled meanwhile, so a change made
                # during a cold `npx` is picked up right after it lands.
                if mcp_task is not None and not mcp_task.done():
                    continue
                forced = state.get("mcp_reload") == "1"
                if forced:
                    await state.set("mcp_reload", "0")
                changed = watcher.poll()
                if not (forced or changed):
                    continue
                logger.info(
                    "mcp reload: %s",
                    "requested" if forced else f"{cfg_path.name} changed",
                )
                await reload_mcp(settings, loop, state)
                if forced:
                    # A forced reload has already read the file; without
                    # this the same edit would fire a second reload.
                    watcher.accept()
            except Exception:
                logger.exception("mcp poll failed (non-fatal)")

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
    # (the stop event and its signal handlers are installed before the
    # boot, above — a boot that waits must still be interruptible)

    await state.set("running", "true")
    _vs("listening")
    duplex = "full duplex" if loop._duplex else "half duplex"
    logger.info("spine engine up (%s, wake=%s)", duplex, loop.wake is not None)

    poller = asyncio.create_task(_poll_inject(), name="spine-inject-poll")
    role_poller = asyncio.create_task(_poll_role(), name="spine-role-poll")
    ctl_poller = asyncio.create_task(_poll_controls(), name="spine-ctl-poll")
    mcp_poller = (
        asyncio.create_task(_poll_mcp(), name="spine-mcp-poll") if mcp_on else None
    )
    runner = asyncio.create_task(loop.run(), name="spine-run")

    # -- "I am alive", out loud ----------------------------------------
    #
    # The legacy engine spoke on start-up; the spine's only sign of life
    # was a green dot on a page nobody had open. It goes through the
    # loop's own service-phrase path (_say_now → _speak, which this
    # runner has already wrapped), so it is muted, interrupted and
    # recorded exactly like any other short phrase — no second TTS path.
    # Its own task: _say_now waits for the playback to drain, and boot
    # must not.
    greet_task: asyncio.Task | None = None
    say_now = getattr(loop, "_say_now", None)
    greeting = greeting_text(settings)
    if not _greeting_enabled(settings):
        logger.info("startup greeting: off")
        publish_boot_status(state, ok=True, phase="listening", greeting="off")
    elif not callable(say_now):
        # Nothing to say it with — say so rather than guess.
        logger.warning("startup greeting: loop has no _say_now — not spoken")
        publish_boot_status(
            state, ok=True, phase="listening", greeting="unreachable"
        )
    else:
        async def _greet() -> None:
            await asyncio.sleep(GREETING_DELAY_SECS)
            try:
                await say_now(greeting)
                logger.info("startup greeting spoken: %r", greeting)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("startup greeting failed (non-fatal)")

        greet_task = asyncio.create_task(_greet(), name="spine-greeting")
        publish_boot_status(state, ok=True, phase="listening", greeting=greeting)
    try:
        waiter = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait(
            {runner, waiter}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in done:
            # .exception() re-raises CancelledError on a cancelled task,
            # so a normal shutdown must not ask.
            if t is runner and not t.cancelled():
                exc = t.exception()
                if exc is not None:
                    raise exc
    finally:
        # mcp_task may still be waiting on a cold `npx`; cancelling it
        # is why it is a named task and not a fire-and-forget coroutine.
        background = [t for t in (poller, role_poller, ctl_poller, mcp_poller,
                                  runner, mcp_task, greet_task) if t is not None]
        for t in background:
            t.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        await audio.stop()
        await _close_loop(loop)
        try:
            await state.set("running", "false")
            _vs("idle")
        except Exception:
            pass
        logger.info("spine engine down")
