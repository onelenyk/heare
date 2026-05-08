"""Heare-memory MCP server.

Exposes the 8 memory tools (`recall`, `remember`, `forget`, `log_action`,
`recent`, `history`, `register_entity`, `link`) over FastMCP.

Two execution modes share this single module:

* **In-process** — the parent voice loop imports the bare functions
  directly (``from src.store.memory.server import recall``) and calls them as
  regular Python callables. They are also registered as MCP tools for
  callers that prefer the protocol envelope.
* **stdio subprocess** — ``python -m src.store.memory.server --stdio`` (or just
  ``python -m src.store.memory.server``) runs ``mcp.run()`` over stdio so spawned
  CLI agents (claude, opencode, hermes) can attach to the same DB
  through ``--mcp-config``.

Concurrency: every tool call opens its own short-lived ``sqlite3``
connection (cheap because of WAL + the OS page cache). No module-level
singleton, no cross-coroutine sharing — each call gets a fresh handle
that lives for the duration of one operation. WAL mode +
``busy_timeout=5000`` (set in ``store.open_memory_db``) handle any
contention between the in-process server and a spawned stdio
subprocess writing to the same SQLite file.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

try:
    from fastmcp import FastMCP
    _FASTMCP_AVAILABLE = True
    mcp = FastMCP("heare-memory")
except ImportError:
    _FASTMCP_AVAILABLE = False
    mcp = None

from src.store.memory import DEFAULT_DB_PATH, open_memory_db


logger = logging.getLogger("heare.memory_mcp")


def _maybe_tool(fn):
    """Register *fn* as a FastMCP tool when fastmcp is installed.

    When fastmcp is NOT installed the function is returned unchanged so
    all eight callables remain usable as plain Python functions.
    """
    if _FASTMCP_AVAILABLE and mcp is not None:
        return mcp.tool()(fn)
    return fn


_VALID_KINDS = {"conversation", "episodic", "semantic"}
_RECENT_KINDS = {
    "conversation",
    "episodic",
    "semantic",
    "action_log",
    "entities",
}


_db_path: Path = DEFAULT_DB_PATH


def configure_db_path(path: Path | str) -> None:
    """Override the DB path before any tool call. Used by tests and by
    the ``--db`` CLI flag.

    Per-call connections (see :func:`_open_conn`) pick up the new path
    on their next invocation, so there is no cached connection to flush.
    """
    global _db_path
    _db_path = Path(path)


def _open_conn() -> sqlite3.Connection:
    """Open a fresh connection for one operation. Caller closes it."""
    return open_memory_db(_db_path)


def _now() -> float:
    return time.time()


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _escape_fts(query: str) -> str:
    """Wrap user text as a phrase so FTS5 grammar tokens (AND/OR/NEAR/
    parens/quotes) don't blow up the parser. Inner double-quotes are
    doubled per the FTS5 spec."""
    escaped = query.replace('"', '""')
    return f'"{escaped}"'


def _to_json(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps(repr(value))


# ---------------------------------------------------------------------------
# Bare callables — both in-process callers (service.py, tool_registry_v6)
# and the FastMCP tool registrations use these directly. The
# ``_maybe_tool`` decorator registers with FastMCP when available while
# keeping ``recall`` etc. callable as plain Python functions.
# ---------------------------------------------------------------------------

@_maybe_tool
def recall(
    query: str,
    kinds: list[str] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Full-text search across conversation/episodic/semantic memory."""
    if not query.strip():
        return []
    requested = set(kinds) if kinds else _VALID_KINDS
    requested &= _VALID_KINDS
    if not requested:
        return []

    placeholders = ",".join("?" for _ in requested)
    sql = (
        "SELECT kind, ref_id, text "
        "FROM fts_memory "
        f"WHERE fts_memory MATCH ? AND kind IN ({placeholders}) "
        "ORDER BY ref_id DESC LIMIT ?"
    )
    params = (_escape_fts(query), *requested, limit)
    conn = _open_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [_row(r) for r in rows]


@_maybe_tool
def remember(
    kind: str,
    content: str,
    tags: str | None = None,
    session_id: str | None = None,
    role: str | None = None,
    key: str | None = None,
) -> dict[str, Any]:
    """Persist a memory under one of the three searchable kinds."""
    ts = _now()
    conn = _open_conn()
    try:
        if kind == "conversation":
            cur = conn.execute(
                "INSERT INTO conversation(session_id, role, content, ts) "
                "VALUES (?, ?, ?, ?)",
                (session_id or "default", role or "user", content, ts),
            )
        elif kind == "episodic":
            cur = conn.execute(
                "INSERT INTO episodic(summary, tags, ts) VALUES (?, ?, ?)",
                (content, tags, ts),
            )
        elif kind == "semantic":
            if not key:
                raise ValueError("semantic remember requires `key`")
            cur = conn.execute(
                "INSERT INTO semantic(key, value, ts) VALUES (?, ?, ?)",
                (key, content, ts),
            )
        else:
            raise ValueError(f"unknown kind: {kind!r}")
        return {"id": cur.lastrowid, "kind": kind, "ts": ts}
    finally:
        conn.close()


@_maybe_tool
def forget(kind: str, id: int) -> dict[str, Any]:
    """Delete a memory by kind+id. FTS5 is updated automatically by triggers."""
    if kind not in _VALID_KINDS:
        raise ValueError(f"forget only supports {sorted(_VALID_KINDS)}")
    conn = _open_conn()
    try:
        if kind == "conversation":
            cur = conn.execute("DELETE FROM conversation WHERE id = ?", (id,))
        elif kind == "episodic":
            cur = conn.execute("DELETE FROM episodic WHERE id = ?", (id,))
        elif kind == "semantic":
            cur = conn.execute("DELETE FROM semantic WHERE id = ?", (id,))
        else:
            raise ValueError(f"Invalid kind: {kind}")
        return {"deleted": cur.rowcount}
    finally:
        conn.close()


@_maybe_tool
def log_action(
    action: str,
    args: Any | None = None,
    result: Any | None = None,
) -> dict[str, Any]:
    """Record a tool invocation in the action_log table."""
    ts = _now()
    conn = _open_conn()
    try:
        cur = conn.execute(
            "INSERT INTO action_log(action, args_json, result_json, ts) "
            "VALUES (?, ?, ?, ?)",
            (action, _to_json(args), _to_json(result), ts),
        )
        return {"id": cur.lastrowid, "ts": ts}
    finally:
        conn.close()


@_maybe_tool
def recent(kind: str, n: int = 10) -> list[dict[str, Any]]:
    """Last n rows of the given kind, newest first."""
    if kind not in _RECENT_KINDS:
        raise ValueError(f"recent only supports {sorted(_RECENT_KINDS)}")
    conn = _open_conn()
    try:
        if kind == "conversation":
            rows = conn.execute(
                "SELECT * FROM conversation ORDER BY ts DESC LIMIT ?", (n,)
            ).fetchall()
        elif kind == "episodic":
            rows = conn.execute(
                "SELECT * FROM episodic ORDER BY ts DESC LIMIT ?", (n,)
            ).fetchall()
        elif kind == "semantic":
            rows = conn.execute(
                "SELECT * FROM semantic ORDER BY ts DESC LIMIT ?", (n,)
            ).fetchall()
        elif kind == "action_log":
            rows = conn.execute(
                "SELECT * FROM action_log ORDER BY ts DESC LIMIT ?", (n,)
            ).fetchall()
        elif kind == "entities":
            rows = conn.execute(
                "SELECT * FROM entities ORDER BY ts DESC LIMIT ?", (n,)
            ).fetchall()
        else:
            raise ValueError(f"Invalid kind: {kind}")
    finally:
        conn.close()
    return [_row(r) for r in rows]


@_maybe_tool
def history(session_id: str) -> list[dict[str, Any]]:
    """Conversation rows for a session, oldest first."""
    conn = _open_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM conversation WHERE session_id = ? ORDER BY ts ASC",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    return [_row(r) for r in rows]


@_maybe_tool
def register_entity(
    name: str,
    kind: str,
    attrs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert an entity row and return its id."""
    ts = _now()
    conn = _open_conn()
    try:
        cur = conn.execute(
            "INSERT INTO entities(name, kind, attrs_json, ts) VALUES (?, ?, ?, ?)",
            (name, kind, _to_json(attrs or {}), ts),
        )
        return {"id": cur.lastrowid, "ts": ts}
    finally:
        conn.close()


@_maybe_tool
def link(src: int, rel: str, dst: int) -> dict[str, Any]:
    """Create a directed relation between two entities."""
    ts = _now()
    conn = _open_conn()
    try:
        cur = conn.execute(
            "INSERT INTO entity_links(src_id, rel, dst_id, ts) VALUES (?, ?, ?, ?)",
            (src, rel, dst, ts),
        )
        return {"id": cur.lastrowid, "ts": ts}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# stdio entry point
# ---------------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    if not _FASTMCP_AVAILABLE:
        print(
            "fastmcp not installed; install with: pip install heare[memory]",
            file=sys.stderr,
        )
        return 1

    parser = argparse.ArgumentParser(description="Heare-memory MCP server")
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="run as a stdio MCP server (default if no other transport given)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="override memory DB path (default: $HEARE_MEMORY_DB or ~/.heare/memory.db)",
    )
    args = parser.parse_args(argv)
    if args.db is not None:
        configure_db_path(args.db)
    # FastMCP.run() defaults to stdio transport, which is what we want for
    # subprocess injection. The flag exists so the CLI shape matches the
    # plan and so we can add other transports later without breaking
    # callers that already pass --stdio.
    mcp.run()
    return 0


if __name__ == "__main__":
    sys.exit(_main())


__all__ = [
    "configure_db_path",
    "forget",
    "history",
    "link",
    "log_action",
    "mcp",
    "recall",
    "recent",
    "register_entity",
    "remember",
]
