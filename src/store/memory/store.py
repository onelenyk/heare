"""Connection helper for the heare memory database.

Synchronous ``sqlite3`` is used on purpose: FastMCP tool handlers are
async but the underlying queries are sub-millisecond and benefit from
the lower per-call overhead vs ``aiosqlite``. If a query ever blocks
the audio loop for >10 ms we wrap that one call site in
``asyncio.to_thread`` rather than retrofitting the whole module.

The MCP tool layer (``src/store/memory/server.py``, US-002) is the only intended
caller; tests in ``tests/test_memory_store.py`` exercise the
connection directly to assert PRAGMA state and FTS behaviour.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


SCHEMA_PATH = Path(__file__).parent / "schema.sql"

DEFAULT_DB_PATH = Path(
    os.environ.get(
        "HEARE_MEMORY_DB",
        str(Path.home() / ".heare" / "memory.db"),
    )
)


def open_memory_db(path: Path | str | None = None) -> sqlite3.Connection:
    """Open (or create) the memory DB and return a configured connection.

    Pragmas applied on every reopen so concurrent access from the
    in-process server and the spawned MCP subprocess stays safe:

    * ``journal_mode=WAL`` — readers don't block the writer.
    * ``busy_timeout=5000`` — each statement waits up to 5 s on a lock
      before raising ``OperationalError`` (gives the other process time
      to commit instead of failing fast).
    * ``foreign_keys=ON`` — entity_links integrity.
    """
    db_path = Path(path) if path is not None else DEFAULT_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        str(db_path),
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_PATH.read_text())
    return conn


__all__ = ["DEFAULT_DB_PATH", "SCHEMA_PATH", "open_memory_db"]
