"""Live status dashboard for a running heare daemon.

Renders a rich.live dashboard from the daemon's SQLite DB, pid file, and
daemon.log. Read-only — the viewer never touches daemon state.
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .config import Settings


def _fmt_time(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _truncate(text: str | None, limit: int) -> str:
    if text is None:
        return ""
    text = str(text).replace("\n", " ")
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _daemon_status(settings: Settings) -> tuple[bool, int | None, str]:
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
        return False, pid, "-"
    started = pid_file.stat().st_mtime
    delta = int(time.time() - started)
    if delta < 60:
        uptime = f"{delta}s"
    elif delta < 3600:
        uptime = f"{delta // 60}m{delta % 60:02d}s"
    else:
        uptime = f"{delta // 3600}h{(delta % 3600) // 60:02d}m"
    return True, pid, uptime


def _current_mode(settings: Settings) -> str:
    if settings.mode_file.exists():
        raw = settings.mode_file.read_text().strip()
        if raw:
            return raw
    return settings.mode.value


def _open_db(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.5)
    except sqlite3.Error:
        return None


def _fetch(con: sqlite3.Connection, sql: str, *params: Any) -> list[tuple]:
    try:
        return con.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []


def _counts(con: sqlite3.Connection | None) -> dict[str, int]:
    out = {"transcripts": 0, "decisions": 0, "actions": 0, "heartbeats": 0}
    if con is None:
        return out
    for name in out:
        rows = _fetch(con, f"SELECT COUNT(*) FROM {name}")
        out[name] = rows[0][0] if rows else 0
    return out


def _build_header(settings: Settings, con: sqlite3.Connection | None) -> Panel:
    running, pid, uptime = _daemon_status(settings)
    mode = _current_mode(settings)
    counts = _counts(con)

    status_text = (
        Text("● running", style="bold green") if running else Text("○ stopped", style="bold red")
    )
    mode_style = {"silent": "dim", "focus": "cyan", "ambient": "yellow"}.get(mode, "white")
    mode_text = Text(mode, style=f"bold {mode_style}")

    line1 = Text.assemble(
        ("heare ", "bold magenta"),
        ("🪶  ", ""),
        status_text,
        ("   pid=", "dim"),
        (f"{pid or '-'}", "white"),
        ("   uptime=", "dim"),
        (uptime, "white"),
        ("   mode=", "dim"),
        mode_text,
    )
    line2 = Text.assemble(
        ("transcripts=", "dim"),
        (f"{counts['transcripts']}", "white"),
        ("  decisions=", "dim"),
        (f"{counts['decisions']}", "white"),
        ("  actions=", "dim"),
        (f"{counts['actions']}", "white"),
        ("  heartbeats=", "dim"),
        (f"{counts['heartbeats']}", "white"),
    )
    return Panel(
        Group(line1, line2),
        title="heare",
        border_style="magenta",
        padding=(0, 1),
    )


def _transcripts_table(con: sqlite3.Connection | None) -> Table:
    table = Table(title="transcripts", expand=True, header_style="bold cyan")
    table.add_column("time", width=8, style="dim")
    table.add_column("mode", width=8, style="dim")
    table.add_column("text", overflow="ellipsis")
    if con is not None:
        rows = _fetch(
            con,
            "SELECT ts, mode, text FROM transcripts ORDER BY ts DESC LIMIT 8",
        )
        for ts, mode, text in reversed(rows):
            table.add_row(_fmt_time(ts), mode, _truncate(text, 80))
    if con is None or not rows:
        table.add_row("—", "—", Text("(none yet)", style="dim italic"))
    return table


def _decisions_table(con: sqlite3.Connection | None) -> Table:
    table = Table(title="decisions", expand=True, header_style="bold cyan")
    table.add_column("time", width=8, style="dim")
    table.add_column("type", width=7)
    table.add_column("conf", width=5, justify="right")
    table.add_column("reply / intent", overflow="ellipsis")
    if con is not None:
        rows = _fetch(
            con,
            """
            SELECT ts, type, confidence, reply, intent
            FROM decisions
            ORDER BY ts DESC LIMIT 8
            """,
        )
        for ts, d_type, conf, reply, intent in reversed(rows):
            conf_s = f"{conf:.2f}" if isinstance(conf, (int, float)) else "--"
            payload = reply or intent or ""
            color = {
                "nothing": "dim",
                "speak": "green",
                "act": "yellow",
            }.get(d_type, "white")
            table.add_row(
                _fmt_time(ts),
                Text(d_type, style=color),
                conf_s,
                _truncate(payload, 80),
            )
    if con is None or not rows:
        table.add_row("—", "—", "—", Text("(none yet)", style="dim italic"))
    return table


def _actions_table(con: sqlite3.Connection | None) -> Table:
    table = Table(title="actions", expand=True, header_style="bold cyan")
    table.add_column("time", width=8, style="dim")
    table.add_column("status", width=9)
    table.add_column("result", overflow="ellipsis")
    if con is not None:
        rows = _fetch(
            con,
            "SELECT ts, status, result_summary FROM actions ORDER BY ts DESC LIMIT 5",
        )
        for ts, status, summary in reversed(rows):
            color = {"ok": "green", "error": "red", "cancelled": "dim"}.get(status, "white")
            table.add_row(
                _fmt_time(ts),
                Text(status, style=color),
                _truncate(summary, 80),
            )
    if con is None or not rows:
        table.add_row("—", "—", Text("(none yet)", style="dim italic"))
    return table


def _heartbeats_table(con: sqlite3.Connection | None) -> Table:
    table = Table(title="heartbeats", expand=True, header_style="bold cyan")
    table.add_column("time", width=8, style="dim")
    table.add_column("decision", width=8)
    table.add_column("reply", overflow="ellipsis")
    if con is not None:
        rows = _fetch(
            con,
            "SELECT ts, decided_to_speak, reply FROM heartbeats ORDER BY ts DESC LIMIT 5",
        )
        for ts, spoke, reply in reversed(rows):
            decision = Text("spoke", style="green") if spoke else Text("quiet", style="dim")
            table.add_row(_fmt_time(ts), decision, _truncate(reply, 80) if reply else "")
    if con is None or not rows:
        table.add_row("—", "—", Text("(none yet)", style="dim italic"))
    return table


def _log_panel(settings: Settings, lines: int = 8) -> Panel:
    log_file = settings.log_dir / "daemon.log"
    content: list[Text] = []
    if log_file.exists():
        try:
            raw = log_file.read_text().splitlines()[-lines:]
        except OSError:
            raw = []
        for line in raw:
            style = ""
            if " ERROR " in line or "ERROR" in line.split()[:4] if line else False:
                style = "red"
            elif " WARNING " in line:
                style = "yellow"
            elif " INFO " in line:
                style = "cyan"
            content.append(Text(_truncate(line, 160), style=style))
    if not content:
        content = [Text("(no log yet)", style="dim italic")]
    return Panel(
        Group(*content),
        title="daemon.log (tail)",
        border_style="dim",
        padding=(0, 1),
    )


def _build_layout(settings: Settings) -> Layout:
    con = _open_db(settings.db_path)
    try:
        layout = Layout()
        layout.split_column(
            Layout(_build_header(settings, con), size=4, name="header"),
            Layout(name="body", ratio=1),
            Layout(_log_panel(settings), size=10, name="log"),
        )
        layout["body"].split_row(
            Layout(
                Group(_transcripts_table(con), _heartbeats_table(con)),
                name="left",
            ),
            Layout(
                Group(_decisions_table(con), _actions_table(con)),
                name="right",
            ),
        )
        return layout
    finally:
        if con is not None:
            con.close()


def run_watch(settings: Settings, interval: float, once: bool = False) -> int:
    console = Console()
    if once:
        console.print(_build_layout(settings))
        return 0
    try:
        with Live(
            _build_layout(settings),
            console=console,
            refresh_per_second=max(1.0, 1.0 / interval) if interval > 0 else 4.0,
            screen=True,
        ) as live:
            while True:
                time.sleep(interval)
                live.update(_build_layout(settings))
    except KeyboardInterrupt:
        return 0
