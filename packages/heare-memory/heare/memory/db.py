"""How this package opens and writes ``heare.db``.

Three writers share the file — the brain, the memory CLI and the
learning scheduler — and SQLite admits one writer at a time. A backup
taking a snapshot, a long facts transaction or another process holding
the write lock makes the next write wait. SQLite's implicit wait is a
few seconds and a Python connection's ``timeout`` is the same; without a
name on it the only visible outcome is ``database is locked`` escaping
``Book.keep``. Every connection this package opens therefore gets the
same named ``busy_timeout``, and the writes that can collide share one
bounded retry with backoff instead of each caller inventing its own.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

logger = logging.getLogger("heare.memory.db")

T = TypeVar("T")

# Five seconds is the C library's default; naming it makes the intent
# visible and gives the retry a base to wait on. A backup or a facts
# pass can outlast any single wait, so a write still retries.
BUSY_TIMEOUT_MS = 5_000
RETRY_ATTEMPTS = 4
RETRY_BACKOFF_S = 0.05


def connect(path: str | Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """A connection with this package's busy policy already in force."""
    conn = sqlite3.connect(
        str(path),
        timeout=BUSY_TIMEOUT_MS / 1_000,
        check_same_thread=check_same_thread,
    )
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


def _is_busy(exc: sqlite3.OperationalError) -> bool:
    text = str(exc).lower()
    return "locked" in text or "busy" in text


def retry_busy(
    work: Callable[[], T], *, on_retry: Callable[[], None] | None = None
) -> T:
    """Run one write again while another connection holds the lock.

    ``on_retry`` runs before the sleep: a caller holding one shared
    connection passes its rollback there, so the next attempt starts
    from a clean transaction instead of finishing a half-written one.
    Only lock contention is retried; any other operational error is the
    caller's to see.
    """
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return work()
        except sqlite3.OperationalError as exc:
            if not _is_busy(exc) or attempt + 1 >= RETRY_ATTEMPTS:
                raise
            if on_retry is not None:
                on_retry()
            delay = RETRY_BACKOFF_S * (2**attempt)
            logger.warning(
                "sqlite busy (attempt %d/%d), retrying in %.2fs",
                attempt + 1,
                RETRY_ATTEMPTS,
                delay,
            )
            time.sleep(delay)
    raise AssertionError("retry_busy ran out of attempts")
