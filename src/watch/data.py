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

from src.config import HEARE_HOME, Settings


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
    """Return the current mode string — mode is now in State, not file."""
    return "focus"


def current_provider(settings: Settings) -> str:
    """Return the current LLM provider — provider is now in State, not file."""
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
    chrome_attached: bool
    bot_muted: bool = False
    mic_muted: bool = False


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


@dataclass(frozen=True)
class VoiceStateData:
    """Live STT state for the voice-state widget.

    Written by ``VoiceStateObserver`` to ``State`` on
    every frame transition; read here on each dashboard tick.
    ``state`` is one of ``idle / listening / stt / result``.
    ``since_ts`` lets the widget auto-decay ``result`` back to idle
    after a short window without the writer needing a timer.
    """

    state: str
    since_ts: float
    last_partial: str | None
    last_final: str | None


def bridge_connected(settings: Settings) -> bool:
    """Return True iff the browser-bridge status file says we have a live
    extension connection. False on missing/malformed file.

    Status file is written by `src/agent/browser_bridge.py` on every
    connect/disconnect transition. We trust `connected=true` regardless
    of timestamp - the status updates on state change.
    """
    path = HEARE_HOME / "browser_bridge.status"
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, OSError, ValueError):
        return False
    if not isinstance(raw, dict):
        return False
    return bool(raw.get("connected"))


@dataclass(frozen=True)
class PairCodeData:
    """Pair code state for the browser bridge pairing flow.

    Written by `src/agent/browser_bridge.py` to the status file.
    ``code`` is the 6-digit pair code or ``None`` if not active.
    ``remaining_s`` is the TTL in seconds (0-60).
    """
    code: str | None
    remaining_s: float


def read_pair_code() -> PairCodeData:
    """Read the pair code from browser_bridge.status. Returns defaults on missing."""
    path = HEARE_HOME / "browser_bridge.status"
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, OSError, ValueError):
        return PairCodeData(code=None, remaining_s=0.0)
    code = raw.get("pair_code")
    remaining = raw.get("pair_remaining_s", 0.0)
    return PairCodeData(
        code=str(code) if code else None,
        remaining_s=float(remaining) if remaining else 0.0,
    )


def read_voice_state(path: Path) -> VoiceStateData:
    """Read the on-disk voice state. Returns idle on missing/corrupt file."""
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, OSError, ValueError):
        return VoiceStateData(state="idle", since_ts=0.0, last_partial=None, last_final=None)
    return VoiceStateData(
        state=str(raw.get("state", "idle")),
        since_ts=float(raw.get("since_ts", 0.0)),
        last_partial=raw.get("last_partial"),
        last_final=raw.get("last_final"),
    )


@dataclass(frozen=True)
class AgentResponseData:
    """Latest agent text response — the read side of the text-response
    channel. The assistant response logger persists every LLM answer to
    the transcripts table (speaker_id='bot') tagged with the active mode
    and whether TTS spoke it, so the dashboard can show what the agent
    said/would say even when muted (silent/meeting). ``text`` is None
    when no bot response exists yet.
    """

    text: str | None
    ts: float
    mode: str | None
    spoken: bool | None


def fetch_agent_response(con: "sqlite3.Connection | None") -> AgentResponseData:
    """Most recent speaker_id='bot' row. Empty on missing rows/columns
    (fetch() swallows sqlite errors, so an un-migrated DB is safe)."""
    empty = AgentResponseData(text=None, ts=0.0, mode=None, spoken=None)
    if con is None:
        return empty
    rows = fetch(
        con,
        "SELECT ts, text, agent_mode, agent_spoken FROM transcripts"
        " WHERE speaker_id = 'bot' ORDER BY ts DESC LIMIT 1",
    )
    if not rows:
        return empty
    ts, text, mode, spoken = rows[0]
    return AgentResponseData(
        text=text,
        ts=float(ts or 0.0),
        mode=mode,
        spoken=None if spoken is None else bool(spoken),
    )


@dataclass(frozen=True)
class DisplayData:
    """Latest rich display block the agent pushed via show_display.

    Latest-only channel: the displays table keeps every block but the
    dashboard renders only the newest. ``content`` is None when the
    agent has not shown anything yet (or the table is absent on an
    un-migrated DB — fetch() swallows the error)."""

    content: str | None
    fmt: str
    title: str | None
    ts: float


def fetch_latest_display(con: "sqlite3.Connection | None") -> DisplayData:
    empty = DisplayData(content=None, fmt="text", title=None, ts=0.0)
    if con is None:
        return empty
    rows = fetch(
        con,
        "SELECT ts, title, format, content FROM displays"
        " ORDER BY ts DESC LIMIT 1",
    )
    if not rows:
        return empty
    ts, title, fmt, content = rows[0]
    return DisplayData(
        content=content,
        fmt=fmt or "text",
        title=title,
        ts=float(ts or 0.0),
    )


@dataclass(frozen=True)
class AudioEventData:
    """Most recent confirmed audio event from the YAMNet observer.

    Written by ``src/audio_event/writer.py`` to
    ``settings.audio_event_file`` on every confirmed event; read here
    on each dashboard tick. ``label`` is ``None`` when no event has
    fired yet (or the file is missing). ``ts`` lets the widget
    auto-decay stale entries without a writer-side timer.
    """

    label: str | None
    score: float
    ts: float


def read_audio_event(path: Path) -> AudioEventData:
    """Read the on-disk audio event. Returns defaults on missing/corrupt file."""
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, OSError, ValueError):
        return AudioEventData(label=None, score=0.0, ts=0.0)
    return AudioEventData(
        label=raw.get("label"),
        score=float(raw.get("score", 0.0)),
        ts=float(raw.get("ts", 0.0)),
    )


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
    usage: UsageData
    voice_state: VoiceStateData
    audio_event: AudioEventData
    agent_response: AgentResponseData
    display: DisplayData
    pair_code: PairCodeData


def fetch_usage(con: sqlite3.Connection | None) -> UsageData:
    """Aggregate ``usage_events`` into a UsageData snapshot.

    Returns zeroed UsageData when the DB is unavailable or the table
    doesn't exist yet (older DB without USE-001 migration). The
    aggregation is index-bound on ``idx_usage_events_kind`` so this
    stays cheap even on long-running daemons.
    """
    zero = UsageData(
        llm_calls=0, llm_input_tokens=0, llm_output_tokens=0, llm_cost_usd=0.0,
        stt_calls=0, stt_audio_seconds=0.0, stt_cost_usd=0.0,
        tts_calls=0, tts_char_count=0, tts_cost_usd=0.0,
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


def fetch_dashboard_state(settings: Settings) -> DashboardSnapshot:
    """Fetch complete dashboard state in a single call.

    Returns a frozen DashboardSnapshot with all data needed by widgets.
    This isolation makes background threading trivial if needed.
    """
    from src.agent.identity import load_identity
    from src.pipeline.stages.mute_gate import is_input_muted

    # Open DB
    con = open_db(settings.db_path)

    # Fetch header data
    running, pid, uptime = daemon_status(settings)
    mode = current_mode(settings)
    provider = current_provider(settings)
    count_dict = counts(con)
    chrome_attached = bridge_connected(settings)
    identity = load_identity(settings.identity_file)
    name = identity["name"] if identity else "heare"
    emoji = identity["emoji"] if identity else "🪶"

    # Mute states — surfaced in the header so the operator can observe
    # bot/mic mute live (toggled via m/M from any process).
    is_muted_val = False  # mute state is now in State, not flag files
    is_input_muted_val = False

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
        chrome_attached=chrome_attached,
        bot_muted=is_muted_val,
        mic_muted=is_input_muted_val,
    )

    # Fetch activity
    activity_rows = fetch_activity(con, limit=50, speakers_file=None)

    # Fetch log tail
    log_lines = read_log_tail(settings.log_dir / "daemon.log", lines=20)

    # Fetch usage / cost ledger
    usage = fetch_usage(con)

    voice_state = VoiceStateData(state="idle", since_ts=0.0, last_partial=None, last_final=None)  # voice state is now in State, not file
    audio_event = read_audio_event(settings.audio_event_file)
    agent_response = fetch_agent_response(con)
    display = fetch_latest_display(con)

    pair_code = read_pair_code()

    # Close DB if open
    if con is not None:
        con.close()

    return DashboardSnapshot(
        header=header,
        activity_rows=activity_rows,
        log_lines=log_lines,
        is_muted=is_muted_val,
        is_input_muted=is_input_muted_val,
        usage=usage,
        voice_state=voice_state,
        audio_event=audio_event,
        agent_response=agent_response,
        display=display,
        pair_code=pair_code,
    )


# ---------------------------------------------------------------------------
# Public API: Plain-text snapshot formatter (for copy-to-clipboard)
# ---------------------------------------------------------------------------


def format_snapshot_text(snapshot: DashboardSnapshot) -> str:
    """Render a ``DashboardSnapshot`` as readable plain text.

    Produces a clean text representation of every widget on the dashboard
    without any escape codes or box-drawing characters, suitable for
    clipboard paste into an email, bug report, or chat message.
    """
    lines: list[str] = []

    # ── Header ────────────────────────────────────────────────────────
    h = snapshot.header
    status = "running" if h.running else "stopped"
    bot_label = "muted" if h.bot_muted else "live"
    mic_label = "muted" if h.mic_muted else "live"
    chrome_label = "connected" if h.chrome_attached else "disconnected"
    pair = snapshot.pair_code
    pair_info = f"  pair-code: {pair.code} ({pair.remaining_s:.0f}s)" if pair.code else ""

    lines.append(f"{h.name} {h.emoji}  {status}   pid={h.pid or '-'}   uptime={h.uptime}")
    lines.append(f"mode={h.mode}   provider={h.provider}   chrome={chrome_label}{pair_info}")
    lines.append(f"transcripts={h.transcripts_count}   actions={h.actions_count}")
    lines.append(f"bot={bot_label}   mic={mic_label}")
    lines.append("")

    # ── Agent response ─────────────────────────────────────────────────
    ar = snapshot.agent_response
    if ar.text:
        spoken_label = ("spoken" if ar.spoken is True
                        else "silent" if ar.spoken is False
                        else "?")
        lines.append(f"--- Agent response ({spoken_label}) ---")
        lines.append(ar.text.strip().replace("\n", " "))
        lines.append("")

    # ── Display panel ──────────────────────────────────────────────────
    dp = snapshot.display
    if dp.content:
        lines.append(f"--- Display ({dp.fmt}) ---")
        if dp.title:
            lines.append(f"title: {dp.title}")
        lines.append(dp.content)
        lines.append("")

    # ── Activity feed ──────────────────────────────────────────────────
    lines.append(f"--- Activity (most recent {len(snapshot.activity_rows)}) ---")
    if not snapshot.activity_rows:
        lines.append("(no activity yet)")
    else:
        # Header
        lines.append(f"{'Time':>8s}  {'WHO':<12s}  {'TYPE':<10s}  Content")
        lines.append(f"{'-'*8}  {'-'*12}  {'-'*10}  {'-'*60}")
        for row in snapshot.activity_rows:
            ts_str = fmt_time(row.ts)
            content = row.content.replace("\n", " ") if row.content else ""
            if len(content) > 80:
                content = content[:77] + "..."
            lines.append(f"{ts_str:>8s}  {row.who:<12s}  {row.type_:<10s}  {content}")
    lines.append("")

    # ── Voice state ────────────────────────────────────────────────────
    vs = snapshot.voice_state
    lines.append("--- Voice state ---")
    lines.append(f"state: {vs.state}")
    if vs.last_partial:
        lines.append(f"partial: {vs.last_partial[:80]}")
    if vs.last_final:
        lines.append(f"final: {vs.last_final[:80]}")
    ae = snapshot.audio_event
    if ae.label is not None:
        lines.append(f"audio event: {ae.label} ({ae.score:.2f})")
    lines.append("")

    # ── Usage / cost ───────────────────────────────────────────────────
    u = snapshot.usage
    lines.append("--- Usage ---")
    lines.append(f"LLM: {u.llm_calls} calls, {u.llm_input_tokens} in / {u.llm_output_tokens} out  ${u.llm_cost_usd:.4f}")
    lines.append(f"STT: {u.stt_calls} calls, {u.stt_audio_seconds:.1f}s audio  ${u.stt_cost_usd:.4f}")
    tts_cost = f"${u.tts_cost_usd:.4f}" if u.tts_cost_usd > 0 else "free"
    lines.append(f"TTS: {u.tts_calls} calls  {tts_cost}")
    lines.append(f"total: ${u.total_cost_usd:.4f}")
    lines.append("")

    # ── Log tail ───────────────────────────────────────────────────────
    lines.append(f"--- Log tail (last {len(snapshot.log_lines)}) ---")
    if not snapshot.log_lines:
        lines.append("(no log lines)")
    else:
        for lf in snapshot.log_lines:
            lines.append(lf.text)
    lines.append("")

    return "\n".join(lines)
