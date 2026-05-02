"""Dashboard widgets for Textual watch TUI."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import RenderableType
from rich.text import Text
from textual.widgets import Static

from ..config import Settings
from .data import HeaderData


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
