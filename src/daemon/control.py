"""Daemon-self-control helpers for the ``stop_daemon`` and
``restart_daemon`` LLM tools.

A process cannot reliably restart itself on Unix without help: any child
spawned by the daemon dies (or gets reparented unpredictably) when the
daemon exits, so the obvious ``make restart`` from a bash tool
terminates the agent without bringing up a new one. The helpers here
solve that with the standard pattern:

* ``schedule_self_exit`` — fire ``SIGTERM`` to ``os.getpid()`` after a
  delay so the daemon's existing graceful-shutdown handlers
  (`src/main.py`'s ``_handle_signal``) run, the PID file is removed,
  and any in-flight TTS finishes playing.
* ``spawn_detached_respawn`` — fork a detached, sessions-leader child
  that waits, then exec's ``hearectl start``. The child uses
  ``start_new_session=True`` (POSIX ``setsid()``) so it gets reparented
  to ``init`` (or ``launchd`` on macOS) when the original daemon dies
  — it survives independently and brings up a fresh daemon.

These helpers stay pure / dependency-free so they can be unit-tested
without spinning up a real pipeline. The LLM-facing dispatchers in
``direct_tools.py`` enforce consent, log, and call these.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
from pathlib import Path
from typing import Optional


logger = logging.getLogger("heare.daemon_control")


def _project_root() -> Path:
    """Return the repo root containing ``hearectl``.

    ``src/daemon_control.py`` lives one level deep, so the parent's
    parent of this file is the project root.
    """
    return Path(__file__).resolve().parent.parent


def _hearectl_path() -> Path:
    return _project_root() / "hearectl"


def spawn_detached_respawn(*, delay_s: float = 1.0) -> int:
    """Spawn a detached child that waits then runs ``hearectl start``.

    Returns the spawned PID. Raises ``FileNotFoundError`` if ``hearectl``
    doesn't exist (e.g. running in a stripped-down container) — callers
    handle that by reporting failure to the LLM rather than killing the
    daemon with no respawn lined up.

    Detachment contract:

    * ``start_new_session=True`` — the child becomes session leader,
      survives the daemon's death by being reparented to PID 1.
    * ``stdin/stdout/stderr=DEVNULL`` — no shared FDs, so closing the
      daemon's stderr cannot SIGPIPE the child.
    * ``close_fds=True`` — defaults to True on Python 3.4+ but we set
      it explicitly to flag intent.
    """
    hearectl = _hearectl_path()
    if not hearectl.exists():
        raise FileNotFoundError(f"hearectl not found at {hearectl}")

    # Pure POSIX shell so we don't inherit the daemon's Python env/venv.
    # ``exec`` replaces the shell with hearectl so the process tree
    # stays shallow (one fewer dangling sh).
    cmd = ["sh", "-c", f"sleep {delay_s:.2f} && exec '{hearectl}' start"]
    proc = subprocess.Popen(
        cmd,
        cwd=str(_project_root()),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    logger.info(
        "[DAEMON CONTROL] spawned detached respawner pid=%d delay=%.2fs",
        proc.pid,
        delay_s,
    )
    return proc.pid


async def schedule_self_exit(
    *,
    delay_s: float = 4.0,
    sig: int = signal.SIGTERM,
    _kill: Optional[callable] = None,
) -> None:
    """After ``delay_s`` seconds, signal the current process.

    Default delay covers a typical "Restarting now" TTS playback so the
    user hears the message before the daemon dies. Default signal is
    ``SIGTERM`` so ``main.py``'s graceful-shutdown handler runs (PID
    file unlinked, in-flight tasks cancelled, transcripts flushed).

    ``_kill`` is a test seam — production code uses ``os.kill``.
    """
    kill_fn = _kill or os.kill
    pid = os.getpid()
    logger.info(
        "[DAEMON CONTROL] self-exit scheduled in %.2fs (sig=%d, pid=%d)",
        delay_s,
        sig,
        pid,
    )
    await asyncio.sleep(delay_s)
    logger.info("[DAEMON CONTROL] firing self-exit signal now")
    kill_fn(pid, sig)


# ---------------------------------------------------------------------------
# Host hooks — set by an embedding host (the menubar app) that owns the
# process and can stop/restart the voice pipeline without killing itself.
# Unset (CLI `start`), stopping the pipeline means exiting the process.

_stop_hook: Optional[callable] = None
_restart_hook: Optional[callable] = None


def set_host_hooks(*, stop: callable, restart: callable) -> None:
    """Register pipeline stop/restart callbacks owned by the host process."""
    global _stop_hook, _restart_hook
    _stop_hook = stop
    _restart_hook = restart


def clear_host_hooks() -> None:
    global _stop_hook, _restart_hook
    _stop_hook = None
    _restart_hook = None


def has_host_hooks() -> bool:
    return _stop_hook is not None and _restart_hook is not None


async def schedule_stop(*, delay_s: float = 4.0) -> str:
    """Stop the voice pipeline after ``delay_s`` (time for TTS to finish).

    With host hooks registered the pipeline stops but the host process
    survives, so the menubar icon stays available to start it again.
    Without them, this is a process exit.
    """
    if _stop_hook is not None:
        await asyncio.sleep(delay_s)
        logger.info("[DAEMON CONTROL] stopping pipeline via host hook")
        _stop_hook()
        return "pipeline"
    await schedule_self_exit(delay_s=delay_s)
    return "process"


async def schedule_restart(*, delay_s: float = 4.0) -> str:
    """Restart the voice pipeline in place via the host, if it can."""
    if _restart_hook is None:
        return "unsupported"
    await asyncio.sleep(delay_s)
    logger.info("[DAEMON CONTROL] restarting pipeline via host hook")
    _restart_hook()
    return "pipeline"


# ---------------------------------------------------------------------------
# Bash safeguard — pattern detection used by direct_tools._execute_bash to
# refuse commands that would shoot the daemon in the foot.

import re  # noqa: E402 — local-by-design, regex constants only

_DANGER_PATTERNS = (
    # ``make restart``, ``make stop`` (with or without other args)
    re.compile(r"\bmake\s+(restart|stop)\b"),
    # Direct hearectl invocation
    re.compile(r"\bhearectl\s+(restart|stop|kill)\b"),
    # ``./hearectl restart``
    re.compile(r"(?:\./|\b)hearectl\s+(restart|stop)\b"),
    # ``kill -<sig>`` / ``kill <pid>``
    re.compile(r"\bkill\s+(?:-[A-Z0-9]+\s+)?\d+\b"),
    # ``pkill src.main`` / ``pkill -f src.main`` — flag is optional and
    # may appear with any spacing pattern.
    re.compile(r"\bpkill\b[^\n]*\bsrc\.main\b"),
    # ``killall python`` / ``killall -9 Python`` / ``killall Python``.
    # Match the whole-line form and rely on ``re.IGNORECASE`` for case.
    re.compile(r"\bkillall\b[^\n]*\bpython\b", re.IGNORECASE),
)


def is_dangerous_self_command(command: str) -> bool:
    """True when ``command`` would terminate or restart the running daemon.

    Used by the bash tool to refuse self-targeting commands; the LLM
    is told to call ``stop_daemon`` / ``restart_daemon`` instead.
    """
    if not command:
        return False
    return any(p.search(command) for p in _DANGER_PATTERNS)


__all__ = [
    "spawn_detached_respawn",
    "schedule_self_exit",
    "is_dangerous_self_command",
]
