"""Live status dashboard for a running heare daemon.

Renders a rich.live dashboard from the daemon's SQLite DB, pid file, and
daemon.log. Read-only — the viewer never touches daemon state.
"""
from __future__ import annotations

import datetime as dt
import json
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
from .identity import load_identity
from .mute_gate import is_input_muted, is_muted, toggle_input_mute, toggle_mute
from .text_injector import inject_text
from .tool_registry import TOOLS
from .watch_controls import daemon_pid, restart_daemon, start_daemon, stop_daemon


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


def _current_provider(settings: Settings) -> str:
    if settings.provider_file.exists():
        raw = settings.provider_file.read_text().strip().lower()
        if raw in ("openrouter", "zai"):
            return raw
    return "openrouter"


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
    out = {"transcripts": 0, "actions": 0}
    if con is None:
        return out
    for name in out:
        rows = _fetch(con, f"SELECT COUNT(*) FROM {name}")
        out[name] = rows[0][0] if rows else 0
    return out


def _build_header(settings: Settings, con: sqlite3.Connection | None) -> Panel:
    running, pid, uptime = _daemon_status(settings)
    mode = _current_mode(settings)
    provider = _current_provider(settings)
    counts = _counts(con)
    identity = load_identity(settings.identity_file)
    name = identity["name"] if identity else "heare"
    emoji = identity["emoji"] if identity else "🪶"

    status_text = (
        Text("● running", style="bold green") if running else Text("○ stopped", style="bold red")
    )
    mode_style = {"silent": "dim", "focus": "cyan", "ambient": "yellow"}.get(mode, "white")
    mode_text = Text(mode, style=f"bold {mode_style}")
    prov_style = "cyan" if provider == "zai" else "yellow"
    provider_text = Text(provider, style=f"bold {prov_style}")

    line1 = Text.assemble(
        (f"{name} ", "bold magenta"),
        (f"{emoji}  ", ""),
        status_text,
        ("   pid=", "dim"),
        (f"{pid or '-'}", "white"),
        ("   uptime=", "dim"),
        (uptime, "white"),
        ("   mode=", "dim"),
        mode_text,
        ("   provider=", "dim"),
        provider_text,
    )
    line2 = Text.assemble(
        ("transcripts=", "dim"),
        (f"{counts['transcripts']}", "white"),
        ("  actions=", "dim"),
        (f"{counts['actions']}", "white"),
    )
    return Panel(
        Group(line1, line2),
        title="heare",
        border_style="magenta",
        padding=(0, 1),
    )


def _load_speaker_labels(speakers_file: Path) -> dict[str, str]:
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


def _speaker_style(sid: str | None) -> str:
    if sid is None:
        return "dim"
    if sid == "owner":
        return "bold green"
    return "yellow"


def _you_table(
    con: sqlite3.Connection | None, labels: dict[str, str] | None = None
) -> Table:
    labels = labels or {}
    """Column 1 — what the user said, with speaker labels."""
    table = Table(title="🧑 You", expand=True, header_style="bold cyan")
    table.add_column("time", width=8, style="dim")
    table.add_column("who", width=10)
    table.add_column("text", overflow="ellipsis")
    rows: list = []
    if con is not None:
        rows = _fetch(
            con,
            "SELECT ts, speaker_id, text FROM transcripts ORDER BY ts DESC LIMIT 8",
        )
        for ts, sid, text in reversed(rows):
            # Filter out bot responses (they go in the Bot panel)
            if sid == "bot":
                continue
            display = labels.get(sid, sid) if sid else "unknown"
            table.add_row(
                _fmt_time(ts),
                Text(_truncate(display, 10), style=_speaker_style(sid)),
                _truncate(text, 80),
            )
    if con is None or not rows:
        table.add_row("—", "—", Text("(none yet)", style="dim italic"))
    return table


def _bot_table(con: sqlite3.Connection | None) -> Table:
    """Column 2 — what the bot said (assistant responses logged by AssistantResponseProcessor)."""
    table = Table(title="🗣️ Bot said", expand=True, header_style="bold cyan")
    table.add_column("time", width=8, style="dim")
    table.add_column("text", overflow="ellipsis")
    rows: list = []
    if con is not None:
        rows = _fetch(
            con,
            "SELECT ts, text FROM transcripts WHERE speaker_id='bot' ORDER BY ts DESC LIMIT 8",
        )
        for ts, text in reversed(rows):
            table.add_row(
                _fmt_time(ts),
                _truncate(text, 80),
            )
    if con is None or not rows:
        table.add_row("—", Text("(none yet)", style="dim italic"))
    return table


def _did_table(con: sqlite3.Connection | None) -> Table:
    """Column 2 — what heare did (actions from Pipecat-native pipeline)."""
    table = Table(title="⚙️ Heare did", expand=True, header_style="bold cyan")
    table.add_column("time", width=8, style="dim")
    table.add_column("status", width=9)
    table.add_column("command", overflow="ellipsis")
    table.add_column("result", overflow="ellipsis")
    rows: list = []
    if con is not None:
        rows = _fetch(
            con,
            """
            SELECT ts, status, tool, args, result_summary
            FROM actions
            ORDER BY ts DESC LIMIT 8
            """,
        )
        for ts, status, tool, args, summary in reversed(rows):
            color = {"ok": "green", "error": "red", "cancelled": "dim", "done": "green", "pending": "yellow"}.get(
                status, "white"
            )
            # Build command from tool and args (direct from actions table)
            command = "—"
            if tool:
                command = f"{tool}: {args}"
            elif args:
                command = args

            result = summary or ""
            table.add_row(
                _fmt_time(ts),
                Text(status, style=color),
                _truncate(command, 50),
                _truncate(result, 80),
            )
    if con is None or not rows:
        table.add_row(
            "—", "—", "—", Text("(none yet)", style="dim italic")
        )
    return table


def _activity_table(con: sqlite3.Connection | None) -> Table:
    """Combined activity feed showing transcripts and actions in chronological order.

    Shows NEWEST items at the TOP (most recent first).
    """
    table = Table(title="📋 Recent Activity (newest first ↑)", expand=True, header_style="bold cyan")
    table.add_column("time", width=8, style="dim")
    table.add_column("type", width=10)
    table.add_column("content", overflow="ellipsis")

    activities: list = []
    if con is not None:
        # Fetch transcripts (increased from 8 to 20 for scrollability)
        # Include speaker_id to distinguish bot responses
        transcript_rows = _fetch(
            con,
            "SELECT ts, text, speaker_id FROM transcripts ORDER BY ts DESC LIMIT 20",
        )
        for ts, text, speaker_id in transcript_rows:
            activities.append((ts, "transcript", text, speaker_id))

        # Fetch actions (increased from 8 to 20 for scrollability)
        action_rows = _fetch(
            con,
            "SELECT ts, status, tool, args, result_summary FROM actions ORDER BY ts DESC LIMIT 20",
        )
        for ts, status, tool, args, summary in action_rows:
            # Build action description
            if tool:
                desc = f"{tool}: {args}"
                if summary:
                    desc += f" → {summary}"
            else:
                desc = f"Action: {status}"
            activities.append((ts, "action", desc, None))

    # Sort by timestamp (most recent first) and take top 30 (increased from 12)
    activities.sort(key=lambda x: x[0], reverse=True)
    activities = activities[:30]

    # Display WITHOUT reversed() - newest first at top
    for ts, kind, content, speaker_id in activities:
        if kind == "transcript":
            # Distinguish between user and bot transcripts
            if speaker_id == "bot":
                style = "magenta"
                kind_display = "🗣️ Bot"
            else:
                style = "cyan"
                kind_display = "🧑 You"
        else:  # action
            style = "yellow"
            kind_display = "⚙️ Did"

        table.add_row(
            _fmt_time(ts),
            Text(kind_display, style=style),
            _truncate(content, 100),
        )

    if con is None or not activities:
        table.add_row("—", "—", Text("(no activity yet)", style="dim italic"))

    return table


_EXECUTION_STYLE = {
    "direct": "green",
    "claude": "cyan",
    "workflow": "yellow",
    "mcp": "magenta",
}


def _tools_table() -> Table:
    """Reference panel — every enabled tool the agent may invoke."""
    table = Table(title="🛠 Tools", expand=True, header_style="bold cyan", padding=(0, 1))
    table.add_column("name", width=24, style="bold")
    table.add_column("exec", width=8)
    table.add_column("description", overflow="ellipsis")
    table.add_column("name", width=24, style="bold")
    table.add_column("exec", width=8)
    table.add_column("description ", overflow="ellipsis")

    enabled = sorted((t for t in TOOLS.values() if t.enabled), key=lambda t: t.name)
    half = (len(enabled) + 1) // 2
    left, right = enabled[:half], enabled[half:]
    def _row(tool):
        return [
            tool.name,
            Text(tool.execution, style=_EXECUTION_STYLE.get(tool.execution, "white")),
            _truncate(tool.description, 60),
        ]

    for i in range(half):
        left_cells = _row(left[i])
        right_cells = _row(right[i]) if i < len(right) else ["", "", ""]
        table.add_row(*left_cells, *right_cells)
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


_CONTROL_HINTS = (
    "[s] start  [x] stop  [r] restart  [m] mute-bot  [M] mute-mic  "
    "[t] text  [q] quit"
)


def _controls_panel(
    settings: Settings,
    last_action: str | None,
    input_buffer: str | None = None,
) -> Panel:
    pid = daemon_pid(settings)
    state = (
        Text("● running", style="bold green")
        if pid is not None
        else Text("○ stopped", style="bold red")
    )
    pid_part = Text(f"  pid={pid}", style="dim") if pid is not None else Text("")
    bot_mute = (
        Text("  🔇bot", style="bold magenta")
        if is_muted(settings.mute_file)
        else Text("  🔊bot", style="dim")
    )
    mic_mute = (
        Text("  🎙️OFF", style="bold magenta")
        if is_input_muted(settings.mute_input_file)
        else Text("  🎙️ON", style="dim")
    )

    if input_buffer is not None:
        prompt = Text("  type message — Enter=send, Esc=cancel:  ", style="bold")
        buffer_view = Text(input_buffer + "▌", style="bold white on grey15")
        line = Text.assemble(state, pid_part, bot_mute, mic_mute, prompt, buffer_view)
    else:
        hint = Text(_CONTROL_HINTS, style="cyan")
        msg = (
            Text(f"  ⤷ {last_action}", style="yellow")
            if last_action
            else Text("")
        )
        line = Text.assemble(state, pid_part, bot_mute, mic_mute, "   ", hint, msg)
    return Panel(line, border_style="dim", padding=(0, 1))


def _build_layout(
    settings: Settings,
    last_action: str | None = None,
    input_buffer: str | None = None,
) -> Layout:
    con = _open_db(settings.db_path)
    labels = _load_speaker_labels(settings.speakers_file)
    try:
        layout = Layout()
        # header=3 + activity=10 + body=ratio + log=6 + controls=3
        layout.split_column(
            Layout(_build_header(settings, con), size=3, name="header"),
            Layout(_activity_table(con), size=10, name="activity"),
            Layout(name="body", ratio=1),
            Layout(_log_panel(settings, lines=4), size=6, name="log"),
            Layout(
                _controls_panel(settings, last_action, input_buffer),
                size=3,
                name="controls",
            ),
        )
        # 3-column body: you | bot | did (each gets equal space)
        layout["body"].split_row(
            Layout(_you_table(con, labels), name="you"),
            Layout(_bot_table(con), name="bot"),
            Layout(_did_table(con), name="did"),
        )
        return layout
    finally:
        if con is not None:
            con.close()


def _dispatch_key(settings: Settings, key: str) -> str | None:
    """Map a hotkey to a daemon action; return a short status message.

    Returns None for unknown keys so the dashboard can ignore them silently.
    """
    if key in ("s", "S"):
        return f"start: {start_daemon(settings)}"
    if key in ("x", "X"):
        return f"stop: {stop_daemon(settings)}"
    if key in ("r", "R"):
        return f"restart: {restart_daemon(settings)}"
    if key == "m":
        muted_now = toggle_mute(settings.mute_file)
        return "bot muted (audio off, text still logged)" if muted_now else "bot unmuted"
    if key == "M":
        muted_now = toggle_input_mute(settings.mute_input_file)
        return "mic muted (daemon can't hear you)" if muted_now else "mic unmuted"
    if key == "p":
        pf = settings.provider_file
        current = pf.read_text().strip().lower() if pf.exists() else "openrouter"
        new_provider = "zai" if current == "openrouter" else "openrouter"
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text(new_provider)
        return f"provider: {new_provider} (effective on next utterance)"
    return None


def _make_key_reader():
    """Put stdin in cbreak mode and return (read_fn, restore_fn).

    ``read_fn()`` returns a single key character or '' if nothing available
    within the poll window. ``restore_fn()`` puts the terminal back.
    Falls back to a no-op pair when stdin is not a TTY (e.g. during tests
    or piped runs).
    """
    import select
    import sys

    if not sys.stdin.isatty():
        return (lambda: ""), (lambda: None)

    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    tty.setcbreak(fd)

    def read_key() -> str:
        rlist, _, _ = select.select([sys.stdin], [], [], 0.0)
        if not rlist:
            return ""
        try:
            return sys.stdin.read(1)
        except (OSError, ValueError):
            return ""

    def restore() -> None:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)

    return read_key, restore


def _handle_input_key(
    settings: Settings, buffer: str, key: str
) -> tuple[str | None, str | None]:
    """Process one key in text-input mode.

    Returns ``(new_buffer, action_message)``.

    * ``new_buffer is None`` means "leave input mode."
    * ``action_message`` is non-None when the action should be displayed in the
      controls panel (e.g. injection result).
    """
    # Enter — submit
    if key in ("\r", "\n"):
        text = buffer.strip()
        if not text:
            return None, "text input cancelled (empty)"
        try:
            inject_text(settings.inject_dir, text)
        except Exception as e:  # noqa: BLE001
            return None, f"inject failed: {e}"
        return None, f"injected: {text[:60]}"

    # Esc — cancel
    if key == "\x1b":
        return None, "text input cancelled"

    # Backspace (DEL = 0x7f, BS = 0x08)
    if key in ("\x7f", "\b"):
        return buffer[:-1], None

    # Printable chars only (filter control codes other than handled ones)
    if key.isprintable():
        return buffer + key, None

    # Unknown control: stay in input mode, no change
    return buffer, None


def run_watch(settings: Settings, interval: float, once: bool = False) -> int:
    console = Console()
    if once:
        console.print(_build_layout(settings))
        return 0

    read_key, restore_tty = _make_key_reader()
    last_action: str | None = None
    input_buffer: str | None = None  # None = normal mode; str = input mode

    def render() -> None:
        live.update(_build_layout(settings, last_action, input_buffer))

    try:
        with Live(
            _build_layout(settings, last_action, input_buffer),
            console=console,
            refresh_per_second=max(1.0, 1.0 / interval) if interval > 0 else 4.0,
            screen=True,
        ) as live:
            while True:
                poll_step = 0.05
                elapsed = 0.0
                while elapsed < max(interval, poll_step):
                    key = read_key()
                    if key:
                        if input_buffer is not None:
                            new_buf, action = _handle_input_key(
                                settings, input_buffer, key
                            )
                            input_buffer = new_buf
                            if action is not None:
                                last_action = action
                            render()
                        else:
                            if key in ("q", "Q", "\x03", "\x04"):
                                return 0
                            if key == "t":
                                input_buffer = ""
                                last_action = None
                                render()
                            else:
                                result = _dispatch_key(settings, key)
                                if result is not None:
                                    last_action = result
                                    render()
                    time.sleep(poll_step)
                    elapsed += poll_step
                render()
    except KeyboardInterrupt:
        return 0
    finally:
        restore_tty()
