"""Data layer for watch dashboard - DB access and snapshot generation.

This module extracts all data-access functions from the legacy watch.py
and provides a clean public API for the Textual dashboard widgets.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, NamedTuple

from ..config import Settings


# ---------------------------------------------------------------------------
# Public API: Time/Text formatting
# ---------------------------------------------------------------------------


def fmt_time(ts: float) -> str:
    """Format timestamp as HH:MM:SS."""
    return dt.datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def truncate(text: str | None, limit: int) -> str:
    """Truncate text to limit characters, replacing newlines with spaces."""
    if text is None:
        return ""
    text = str(text).replace("\n", " ")
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


# ---------------------------------------------------------------------------
# Public API: Daemon status
# ---------------------------------------------------------------------------


def daemon_status(settings: Settings) -> tuple[bool, int | None, str]:
    """Check daemon running status from PID file.

    Returns:
        (running, pid, uptime_string)
    """
    pid_file = settings.pid_file
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


# ---------------------------------------------------------------------------
# Public API: Mode/Provider
# ---------------------------------------------------------------------------


def current_mode(settings: Settings) -> str:
    """Get current mode from file or settings."""
    if settings.mode_file.exists():
        raw = settings.mode_file.read_text().strip()
        if raw:
            return raw
    return settings.mode.value


def current_provider(settings: Settings) -> str:
    """Get current LLM provider from file or default."""
    if settings.provider_file.exists():
        raw = settings.provider_file.read_text().strip().lower()
        if raw in ("openrouter", "zai"):
            return raw
    return "openrouter"


# ---------------------------------------------------------------------------
# Public API: Database access
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Public API: Speaker labels
# ---------------------------------------------------------------------------


def load_speaker_labels(speakers_file: Path) -> dict[str, str]:
    """Return {speaker_id: label} from the gallery JSON. Fails silently."""
    if not speakers_file.exists():
        return {}
    try:
        data = json.loads(speakers_file.read_text())
    except (OSError, ValueError):
        return {}
    speakers = data.get("speakers") or {}
    return {
        sid: (entry.get("label") or sid)
        for sid, entry in speakers.items()
        if isinstance(entry, dict)
    }


def speaker_style(sid: str | None) -> str:
    """Get Rich style string for speaker ID."""
    if sid is None:
        return "dim"
    if sid == "owner":
        return "bold green"
    return "yellow"


# ---------------------------------------------------------------------------
# Public API: Activity feed (unified transcripts + actions)
# ---------------------------------------------------------------------------


class StatusBadge(StrEnum):
    """Status badge colors for action TYPE column."""

    OK = "green"
    DONE = "green"
    ERROR = "red"
    CANCELLED = "dim"
    PENDING = "yellow"
    OTHER = "white"


def status_color(status: str | None) -> str:
    """Get Rich color for action status."""
    if status == "ok":
        return StatusBadge.OK
    if status == "done":
        return StatusBadge.DONE
    if status == "error":
        return StatusBadge.ERROR
    if status == "cancelled":
        return StatusBadge.CANCELLED
    if status == "pending":
        return StatusBadge.PENDING
    return StatusBadge.OTHER


class ActivityRow(NamedTuple):
    """Single row in the unified activity feed."""

    ts: float
    who: str  # Display name: "bot", "you", label, or tool name
    type_: str  # "said" for transcripts, status badge text for actions
    content: str  # Truncated text/tool:args
    style: str  # Rich style for WHO column
    status: str | None  # Raw status from actions table (NULL for transcripts)


def fetch_activity(con: sqlite3.Connection | None, limit: int = 50, speakers_file: Path | None = None) -> list[ActivityRow]:
    """Fetch unified activity feed with transcripts and actions.

    Uses UNION ALL query for efficiency. Returns newest first.
    Includes status field from actions for badge rendering.

    Args:
        con: Database connection
        limit: Max rows to fetch
        speakers_file: Optional path to speakers.json for labels
    """
    activities: list[ActivityRow] = []
    if con is None:
        return activities

    # UNION ALL query with status field
    query = """
    SELECT ts, 'said' AS type, text AS content, speaker_id AS who_key,
           NULL AS tool, NULL AS status
    FROM transcripts
    UNION ALL
    SELECT ts, 'did' AS type,
           COALESCE(tool || ': ' || args, 'action') AS content,
           NULL AS who_key,
           tool, status
    FROM actions
    ORDER BY ts DESC
    LIMIT ?
    """

    rows = fetch(con, query, limit)
    labels = load_speaker_labels(speakers_file) if speakers_file else {}

    for ts, type_, content, who_key, tool, status in rows:
        if type_ == "said":
            # Transcript row
            if who_key == "bot":
                who = "bot"
                style = "magenta"
            elif who_key is None:
                who = "you"
                style = "dim"
            else:
                who = labels.get(who_key, who_key)
                style = speaker_style(who_key)
            activities.append(ActivityRow(ts, who, "said", content, style, status))
        else:
            # Action row
            who = tool if tool else "action"
            style = "yellow"
            # Use status as TYPE column content
            type_content = status if status else "done"
            activities.append(ActivityRow(ts, who, type_content, content, style, status))

    return activities


# ---------------------------------------------------------------------------
# Public API: Log tail
# ---------------------------------------------------------------------------


class LogLine(NamedTuple):
    """Single line from daemon.log with severity."""

    text: str
    severity: str


def read_log_tail(log_path: Path, lines: int = 20) -> list[LogLine]:
    """Read last N lines from daemon.log with severity detection.

    Severity is detected from common log prefixes:
    - ERROR, ERR, CRITICAL, CRIT -> "error"
    - WARNING, WARN -> "warning"
    - INFO -> "info"
    - Other -> "default"
    """
    if not log_path.exists():
        return []

    try:
        all_lines = log_path.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    tail_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines

    result: list[LogLine] = []
    for line in tail_lines:
        # Detect severity from log prefix
        upper = line.upper()
        if any(p in upper for p in ("ERROR:", "ERR ", "CRITICAL:", "CRIT ")):
            severity = "error"
        elif any(p in upper for p in ("WARNING:", "WARN ")):
            severity = "warning"
        elif "INFO:" in upper:
            severity = "info"
        else:
            severity = "default"
        result.append(LogLine(line, severity))

    return result


# ---------------------------------------------------------------------------
# Public API: Dashboard snapshot (single-call pattern)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HeaderData:
    """Header information for dashboard."""

    name: str
    emoji: str
    running: bool
    pid: int | None
    uptime: str
    mode: str
    provider: str
    transcripts_count: int
    actions_count: int


@dataclass(frozen=True)
class DashboardSnapshot:
    """Complete dashboard state snapshot.

    All widgets receive this frozen dataclass on refresh.
    Makes future run_worker(thread=True) migration a 1-line change.
    """

    header: HeaderData
    activity_rows: list[ActivityRow]
    log_lines: list[LogLine]
    is_muted: bool
    is_input_muted: bool


def fetch_dashboard_state(settings: Settings) -> DashboardSnapshot:
    """Fetch complete dashboard state in a single call.

    Returns a frozen DashboardSnapshot with all data needed by widgets.
    This isolation makes background threading trivial if needed.
    """
    from ..identity import load_identity
    from ..mute_gate import is_input_muted, is_muted

    # Open DB
    con = open_db(settings.db_path)

    # Fetch header data
    running, pid, uptime = daemon_status(settings)
    mode = current_mode(settings)
    provider = current_provider(settings)
    count_dict = counts(con)
    identity = load_identity(settings.identity_file)
    name = identity["name"] if identity else "heare"
    emoji = identity["emoji"] if identity else "🪶"

    header = HeaderData(
        name=name,
        emoji=emoji,
        running=running,
        pid=pid,
        uptime=uptime,
        mode=mode,
        provider=provider,
        transcripts_count=count_dict["transcripts"],
        actions_count=count_dict["actions"],
    )

    # Fetch activity
    activity_rows = fetch_activity(con, limit=50, speakers_file=settings.speakers_file)

    # Fetch log tail
    log_lines = read_log_tail(settings.log_dir / "daemon.log", lines=20)

    # Fetch mute states
    is_muted_val = is_muted(settings.mute_file)
    is_input_muted_val = is_input_muted(settings.mute_input_file)

    # Close DB if open
    if con is not None:
        con.close()

    return DashboardSnapshot(
        header=header,
        activity_rows=activity_rows,
        log_lines=log_lines,
        is_muted=is_muted_val,
        is_input_muted=is_input_muted_val,
    )
