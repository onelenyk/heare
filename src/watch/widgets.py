"""Dashboard widgets for Textual watch TUI."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import RenderableType
from rich.text import Text
from textual.widgets import DataTable, Static

from ..config import Settings
from .data import ActivityRow, HeaderData, fmt_time


class HeaderBar(Static):
    """Header bar showing daemon status, metadata, and counts.

    Renders the same two-line layout as the legacy _build_header():
    Line 1: name emoji status pid uptime mode provider
    Line 2: transcripts actions
    """

    def __init__(self, settings: Settings) -> None:
        """Initialize header bar.

        Args:
            settings: Application settings
        """
        super().__init__()
        self.settings = settings

    def refresh_data(self, header: HeaderData) -> None:
        """Update header display from dashboard snapshot.

        Args:
            header: Header data from DashboardSnapshot
        """
        # Build status text
        status_text = (
            Text("● running", style="bold green")
            if header.running
            else Text("○ stopped", style="bold red")
        )

        # Mode styling
        mode_style = {"silent": "dim", "focus": "cyan", "ambient": "yellow"}.get(
            header.mode, "white"
        )
        mode_text = Text(header.mode, style=f"bold {mode_style}")

        # Provider styling
        prov_style = "cyan" if header.provider == "zai" else "yellow"
        provider_text = Text(header.provider, style=f"bold {prov_style}")

        # Line 1: name emoji status pid uptime mode provider
        line1 = Text.assemble(
            (f"{header.name} ", "bold magenta"),
            (f"{header.emoji}  ", ""),
            status_text,
            ("   pid=", "dim"),
            (f"{header.pid or '-'}", "white"),
            ("   uptime=", "dim"),
            (header.uptime, "white"),
            ("   mode=", "dim"),
            mode_text,
            ("   provider=", "dim"),
            provider_text,
        )

        # Line 2: transcripts actions
        line2 = Text.assemble(
            ("transcripts=", "dim"),
            (f"{header.transcripts_count}", "white"),
            ("  actions=", "dim"),
            (f"{header.actions_count}", "white"),
        )

        # Update display with both lines
        self.update(Text.assemble(line1, "\n", line2))


class ActivityTable(DataTable):
    """Activity table showing unified feed of transcripts and actions.

    Fixed columns: time (8), WHO (12), TYPE (10), content (flex).
    TYPE column shows status badges for actions with color coding.
    Scrollable by default with Textual's built-in navigation.
    """

    def __init__(self) -> None:
        """Initialize activity table."""
        super().__init__()
        self._setup_columns()

    def _setup_columns(self) -> None:
        """Configure table columns."""
        self.add_column("time", width=8)
        self.add_column("WHO", width=12)
        self.add_column("TYPE", width=10)
        self.add_column("content", width="flex")

    def refresh_data(self, rows: list[ActivityRow]) -> None:
        """Update table with new activity data.

        Args:
            rows: List of ActivityRow from fetch_activity()
        """
        # Clear existing rows
        self.clear()

        # Show placeholder if no data
        if not rows:
            self.add_row(
                "--", "--", "--", Text("(no activity yet)", style="dim italic")
            )
            return

        # Add rows in reverse (newest first, as returned by fetch_activity)
        for row in rows:
            # Time column
            time_str = fmt_time(row.ts)

            # WHO column with styling
            who_text = Text(row.who, style=row.style)

            # TYPE column with status badge styling
            if row.type_ == "said":
                # Transcript: show "said" in cyan/magenta
                type_style = "cyan" if row.who != "bot" else "magenta"
                type_text = Text(row.type_, style=type_style)
            else:
                # Action: show status with color coding
                type_text = Text(row.type_, style=status_color(row.status))

            # Content column
            content_str = row.content[:100] + "…" if len(row.content) > 100 else row.content

            self.add_row(time_str, who_text, type_text, content_str)


def status_color(status: str | None) -> str:
    """Get Rich color for action status badge.

    Args:
        status: Status string from actions table

    Returns:
        Rich style string
    """
    if status == "ok":
        return "green"
    if status == "done":
        return "green"
    if status == "error":
        return "red"
    if status == "cancelled":
        return "dim"
    if status == "pending":
        return "yellow"
    return "white"
