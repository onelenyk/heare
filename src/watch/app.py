"""HeareDashboard - Main Textual app for watch dashboard."""
from __future__ import annotations

from textual.app import App


class HeareDashboard(App):
    """Main dashboard application."""

    CSS_PATH = "dashboard.tcss"
    TITLE = "heare"

    def __init__(self, settings: Any, interval: float = 0.5):
        """Initialize dashboard.

        Args:
            settings: Application settings
            interval: Refresh interval in seconds
        """
        super().__init__()
        self.settings = settings
        self.interval = interval
