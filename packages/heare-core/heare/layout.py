"""Where things live in ``~/.heare``, and the move from where they lived.

Until 25 October everything lay side by side: the owner's memory next to
tokens, sockets and what the old app left. Now it is in sections —
``memory/`` and ``settings/`` are the owner's and go into a backup,
``machine/`` is this Mac's and never does.

Every entry point calls ``settle()`` before it opens anything. The brain
and the keeper start together, so the move happens under a lock and only
the first one does it. A moved thing leaves a symlink where it was, so a
release from before the move (``make rollback``) still finds it. If that
older release then replaced the link with a real file — an atomic write
does that — the next settle keeps whichever copy is newer and puts the
other in ``old/``. Nothing is ever deleted.
"""

from __future__ import annotations

import fcntl
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("heare.layout")

VERSION = "2"

# Where each thing lived → where it lives now, relative to ~/.heare. The old
# memory/ folder held only the profile and the passes' state; its contents
# move one by one, because memory/ itself is now the owner's section.
MOVES: tuple[tuple[str, str], ...] = (
    ("heare.db", "memory/heare.db"),
    ("heare.db-wal", "memory/heare.db-wal"),
    ("heare.db-shm", "memory/heare.db-shm"),
    ("events", "memory/journal"),
    ("heard", "memory/heard"),
    ("intents.json", "memory/reminders.json"),
    ("canvas.json", "memory/canvas.json"),
    ("screens", "memory/screens"),
    ("config.toml", "settings/config.toml"),
    ("identity.json", "settings/identity.json"),
    ("sources.json", "settings/sources.json"),
    ("api_token", "machine/api_token"),
    ("machine_token", "machine/machine_token"),
    ("part_tokens.json", "machine/part_tokens.json"),
    ("pair_codes.json", "machine/pair_codes.json"),
    ("parts.json", "machine/parts.json"),
    ("parts", "machine/parts"),
    ("agent-session.json", "machine/agent-session.json"),
    ("opencode", "machine/opencode"),
    ("keys.json", "machine/keys.json"),
)
PROFILE = ("user.md", "threads.md", "goals.md", "consolidate.json", "review.json")
# What nothing reads any more: kept, out of the way.
LEFTOVERS = ("core.db", "core.db-wal", "core.db-shm", "wheels", "workspace")


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _aside(home: Path, path: Path, why: str) -> None:
    target = home / "old" / f"{why}-{_stamp()}" / path.relative_to(home)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(path, target)
    logger.warning("set %s aside in %s", path.name, target.parent)


def _move(home: Path, old: Path, new: Path, first: bool) -> None:
    if old.is_symlink():
        return
    if not old.exists():
        return
    if new.exists():
        # Both are real. Before the first move, what lived at the old place
        # is the owner's history and wins: something at the new place then
        # is a stray (a newer mtime proves nothing — an empty database made
        # a minute ago is newer than a year of conversation). After the
        # move, a real file at the old place was written by an older
        # release put back by rollback, and the newer of the two wins.
        if first or _mtime(old) > _mtime(new):
            _aside(home, new, "displaced")
        else:
            _aside(home, old, "displaced")
            old.symlink_to(os.path.relpath(new, old.parent))
            return
    new.parent.mkdir(parents=True, exist_ok=True)
    os.replace(old, new)
    old.symlink_to(os.path.relpath(new, old.parent))


def _dead_sockets(home: Path) -> None:
    """Sockets the hands left at the top before the move. A live one belongs
    to a running hands and stays."""
    for path in home.glob("hands-*.sock"):
        try:
            os.kill(int(path.stem.removeprefix("hands-")), 0)
        except ValueError:
            continue
        except ProcessLookupError:
            path.unlink(missing_ok=True)
        except PermissionError:
            continue


def _settle(home: Path) -> None:
    first = not (home / "layout").exists()
    profile = home / "memory" / "profile"
    for name in PROFILE:
        _move(home, home / "memory" / name, profile / name, first)
    for old, new in MOVES:
        _move(home, home / old, home / new, first)
    for name in LEFTOVERS:
        path = home / name
        if path.exists() and not path.is_symlink():
            target = home / "old" / name
            if target.exists():
                _aside(home, path, "leftover")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(path, target)
    _dead_sockets(home)
    for section in ("memory", "settings", "machine"):
        (home / section).mkdir(mode=0o700, parents=True, exist_ok=True)
        (home / section).chmod(0o700)
    (home / "layout").write_text(VERSION + "\n", encoding="utf-8")


def settle(home: Path | None = None) -> None:
    """Put ~/.heare in its sections. Safe to call from every process, every
    start: the second call finds nothing to move."""
    if home is None:
        from heare.config import HOME
        home = HOME
    if not home.exists():
        home.mkdir(mode=0o700, parents=True)
    with open(home / ".settle.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            _settle(home)
        except OSError:
            # A half-moved home still works through its links; the next
            # start finishes the move.
            logger.exception("could not lay out %s", home)
