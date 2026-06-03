"""Watch dashboard - Live status TUI for heare daemon.

Migrated from rich.live to Textual for real scrolling and better interactivity.
"""
from __future__ import annotations

import os

from rich.console import Console
from rich.table import Table

from src.config import Settings
from src.agent.identity import load_identity
from src.agent.llm.providers import PROVIDERS
from .app import HeareDashboard
from src.dashboard_data import fetch_dashboard_state


def run_watch(settings: Settings, interval: float, once: bool = False) -> int:
    """Run the watch dashboard.

    Args:
        settings: Application settings
        interval: Refresh interval in seconds
        once: If True, print snapshot once and exit (bypass TUI)

    Returns:
        Exit code (0 for success)
    """
    # Legacy env-var routing for rollback
    if os.environ.get("HEARE_WATCH_LEGACY") == "1":
        from ._legacy import run_watch as _legacy_run_watch

        return _legacy_run_watch(settings, interval, once)

    # Once mode: render snapshot via rich.Console and exit
    if once:
        return _run_once(settings)

    # Interactive mode: run Textual TUI. Loop while the user requests a
    # respawn (Ctrl+R) so the app gets a fresh instance from scratch.
    while True:
        app = HeareDashboard(settings=settings, interval=interval)
        try:
            result = app.run()
        except Exception:
            return 1
        if result != "__respawn__":
            return 0


def _run_once(settings: Settings) -> int:
    """Render dashboard snapshot once and exit.

    Args:
        settings: Application settings

    Returns:
        Exit code (0 for success)
    """
    # Fetch snapshot
    snapshot = fetch_dashboard_state(settings)

    # Build rich table layout
    console = Console()

    # Header line
    identity = load_identity(settings.identity_file)
    name = identity["name"] if identity else snapshot.header.name
    emoji = identity["emoji"] if identity else snapshot.header.emoji

    status = "● running" if snapshot.header.running else "○ stopped"
    mode_style = {"silent": "dim", "focus": "cyan", "ambient": "yellow"}.get(
        snapshot.header.mode, "white"
    )
    cfg = PROVIDERS.get(snapshot.header.provider)
    provider_style = cfg.dashboard_color if cfg else "yellow"

    header_line = (
        f"{name} {emoji}  {status}   "
        f"pid={snapshot.header.pid or '-'}   "
        f"uptime={snapshot.header.uptime}   "
        f"mode=[{mode_style}]{snapshot.header.mode}[/]   "
        f"provider=[{provider_style}]{snapshot.header.provider}[/]"
    )

    counts_line = (
        f"transcripts={snapshot.header.transcripts_count}  "
        f"actions={snapshot.header.actions_count}"
    )

    console.print(header_line)
    console.print(counts_line)
    console.print()

    # Activity table
    if snapshot.activity_rows:
        table = Table(title="Recent Activity (newest first)", show_header=True, expand=True)
        table.add_column("Time", width=8)
        table.add_column("WHO", width=12)
        table.add_column("TYPE", width=10)
        table.add_column("Content", overflow="ellipsis")

        for row in snapshot.activity_rows[:20]:  # Show last 20
            time_str = row.ts.strftime("%H:%M:%S") if hasattr(row.ts, "strftime") else str(row.ts)
            table.add_row(time_str, row.who, row.type_, row.content[:100])

        console.print(table)
    else:
        console.print("(no activity yet)", style="dim italic")

    console.print()

    # Log tail
    if snapshot.log_lines:
        console.print("Log tail (last 20 lines):")
        for line in snapshot.log_lines[-20:]:
            style = {"error": "red", "warning": "yellow", "info": "cyan"}.get(
                line.severity, "white"
            )
            console.print(f"[{style}]{line.text}[/]")
    else:
        console.print("(no log lines)", style="dim italic")

    return 0
