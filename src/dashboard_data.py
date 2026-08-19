"""Minimal DB access layer for the API: daemon status, usage metrics, DB connection.

This module provides data-access functions for the web API (GET /state, etc).
It replaced a larger module that served a terminal dashboard (Textual TUI), which has been removed.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config import Settings


def daemon_status(settings: Settings) -> tuple[bool, int | None, str]:
    """Check daemon running status from PID file.

    Returns:
        (running, pid, uptime_string)
    """
    pid_file = Path.home() / ".heare" / "heare.pid"
    if not pid_file.exists():
        return False, None, "-"
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return False, None, "-"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False, None, "-"  # Return None for stale PID
    started = pid_file.stat().st_mtime
    delta = int(time.time() - started)
    if delta < 60:
        uptime = f"{delta}s"
    elif delta < 3600:
        uptime = f"{delta // 60}m{delta % 60:02d}s"
    else:
        uptime = f"{delta // 3600}h{(delta % 3600) // 60:02d}m"
    return True, pid, uptime


def open_db(path: Path) -> sqlite3.Connection | None:
    """Open read-only SQLite connection."""
    if not path.exists():
        return None
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.5)
    except sqlite3.Error:
        return None


def fetch(con: sqlite3.Connection, sql: str, *params: Any) -> list[tuple]:
    """Execute SQL and fetch all results."""
    try:
        return con.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []


def counts(con: sqlite3.Connection | None) -> dict[str, int]:
    """Get transcript and action counts from DB."""
    out = {"transcripts": 0, "actions": 0}
    if con is None:
        return out
    for name in out:
        rows = fetch(con, f"SELECT COUNT(*) FROM {name}")
        out[name] = rows[0][0] if rows else 0
    return out


@dataclass(frozen=True)
class UsageData:
    """Aggregated usage / cost numbers for the dashboard's UsageBar.

    Mirrors the shape of ``TranscriptStore.get_usage_summary`` but as
    a frozen dataclass so widgets get type-checked, immutable
    snapshots. Token / second / char counts are sums across the full
    ledger; ``total_cost_usd`` is the running spend.
    """

    llm_calls: int
    llm_input_tokens: int
    llm_output_tokens: int
    llm_cost_usd: float

    stt_calls: int
    stt_audio_seconds: float
    stt_cost_usd: float

    tts_calls: int
    tts_char_count: int
    tts_cost_usd: float

    total_cost_usd: float


def fetch_usage(con: sqlite3.Connection | None) -> UsageData:
    """Aggregate ``usage_events`` into a UsageData snapshot.

    Returns zeroed UsageData when the DB is unavailable or the table
    doesn't exist yet (older DB without USE-001 migration). The
    aggregation is index-bound on ``idx_usage_events_kind`` so this
    stays cheap even on long-running daemons.
    """
    zero = UsageData(
        llm_calls=0,
        llm_input_tokens=0,
        llm_output_tokens=0,
        llm_cost_usd=0.0,
        stt_calls=0,
        stt_audio_seconds=0.0,
        stt_cost_usd=0.0,
        tts_calls=0,
        tts_char_count=0,
        tts_cost_usd=0.0,
        total_cost_usd=0.0,
    )
    if con is None:
        return zero
    try:
        rows = fetch(
            con,
            """
            SELECT kind,
                   COUNT(*),
                   COALESCE(SUM(input_tokens), 0),
                   COALESCE(SUM(output_tokens), 0),
                   COALESCE(SUM(audio_seconds), 0.0),
                   COALESCE(SUM(char_count), 0),
                   COALESCE(SUM(cost_usd), 0.0)
            FROM usage_events
            GROUP BY kind
            """,
        )
    except sqlite3.OperationalError:
        # Table missing on a pre-USE-001 DB; show zeros until first event.
        return zero

    llm = stt = tts = None
    total = 0.0
    for kind, calls, in_tok, out_tok, audio_s, chars, cost in rows:
        cost_f = float(cost or 0.0)
        total += cost_f
        if kind == "llm":
            llm = (int(calls), int(in_tok), int(out_tok), cost_f)
        elif kind == "stt":
            stt = (int(calls), float(audio_s or 0.0), cost_f)
        elif kind == "tts":
            tts = (int(calls), int(chars), cost_f)

    return UsageData(
        llm_calls=llm[0] if llm else 0,
        llm_input_tokens=llm[1] if llm else 0,
        llm_output_tokens=llm[2] if llm else 0,
        llm_cost_usd=llm[3] if llm else 0.0,
        stt_calls=stt[0] if stt else 0,
        stt_audio_seconds=stt[1] if stt else 0.0,
        stt_cost_usd=stt[2] if stt else 0.0,
        tts_calls=tts[0] if tts else 0,
        tts_char_count=tts[1] if tts else 0,
        tts_cost_usd=tts[2] if tts else 0.0,
        total_cost_usd=total,
    )
