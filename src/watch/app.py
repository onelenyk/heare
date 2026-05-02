"""HeareDashboard - Main Textual app for watch dashboard.

This module contains the main App class that assembles all widgets,
binds hotkeys, and manages the refresh loop.
"""
from __future__ import annotations

from textual.app import App
from textual.binding import Binding
from textual.widgets import Footer, Header

from ..config import Settings
from ..mute_gate import toggle_input_mute, toggle_mute
from ..text_injector import inject_text
from ..watch_controls import daemon_pid, restart_daemon, start_daemon, stop_daemon
from .data import DashboardSnapshot, fetch_dashboard_state
from .widgets import ActivityTable, ControlsBar, HeaderBar, LogTail


class HeareDashboard(App):
    """Main dashboard application.

    Assembles all widgets (HeaderBar, ActivityTable, LogTail, ControlsBar)
    and binds hotkeys for daemon control and dashboard interaction.
    """

    CSS_PATH = "dashboard.tcss"
    TITLE = "heare"

    BINDINGS = [
        Binding("s", "start_daemon", "Start", show=True),
        Binding("x", "stop_daemon", "Stop", show=True),
        Binding("r", "restart_daemon", "Restart", show=True),
        Binding("m", "toggle_mute_bot", "Mute bot", show=True),
        Binding("M", "toggle_mute_mic", "Mute mic", show=True),
        Binding("t", "text_input", "Text inject", show=True),
        Binding("p", "toggle_provider", "Provider", show=False),
        Binding("q", "quit", "Quit", show=True),
        Binding("left", "shrink_left", "Shrink left", show=False),
        Binding("right", "grow_left", "Grow left", show=False),
    ]

    def __init__(self, settings: Settings, interval: float = 0.5) -> None:
        """Initialize dashboard.

        Args:
            settings: Application settings
            interval: Refresh interval in seconds
        """
        super().__init__()
        self.settings = settings
        self.interval = interval
        self._refresh_timer = None

    def on_mount(self) -> None:
        """Called when app is mounted. Set up widgets and refresh timer."""
        # Create and mount widgets
        header = HeaderBar(self.settings)
        activity = ActivityTable()
        log_tail = LogTail()
        controls = ControlsBar()

        # Mount in Vertical layout (default is vertical stacking)
        self.mount(header, activity, log_tail, controls)

        # Set up refresh timer
        self._refresh_timer = self.set_interval(self.interval, self._refresh_data)

        # Initial data load
        self._refresh_data()

    def _refresh_data(self) -> None:
        """Fetch fresh data and update all widgets."""
        snapshot = fetch_dashboard_state(self.settings)

        # Update each widget with its data slice
        header = self.query_one(HeaderBar)
        activity = self.query_one(ActivityTable)
        log_tail = self.query_one(LogTail)

        header.refresh_data(snapshot.header)
        activity.refresh_data(snapshot.activity_rows)
        log_tail.refresh_data(snapshot.log_lines)

    # -----------------------------------------------------------------------
    # Daemon control actions
    # -----------------------------------------------------------------------

    def action_start_daemon(self) -> None:
        """Start the daemon (s key)."""
        msg = start_daemon(self.settings)
        self.query_one(ControlsBar).update_status(msg)

    def action_stop_daemon(self) -> None:
        """Stop the daemon (x key)."""
        msg = stop_daemon(self.settings)
        self.query_one(ControlsBar).update_status(msg)

    def action_restart_daemon(self) -> None:
        """Restart the daemon (r key)."""
        msg = restart_daemon(self.settings)
        self.query_one(ControlsBar).update_status(msg)

    def action_toggle_mute_bot(self) -> None:
        """Toggle bot mute (m key)."""
        muted = toggle_mute(self.settings.mute_file)
        msg = "bot muted" if muted else "bot unmuted"
        self.query_one(ControlsBar).update_status(msg)

    def action_toggle_mute_mic(self) -> None:
        """Toggle mic mute (M key, Shift+m)."""
        muted = toggle_input_mute(self.settings.mute_input_file)
        msg = "mic muted" if muted else "mic unmuted"
        self.query_one(ControlsBar).update_status(msg)

    def action_toggle_provider(self) -> None:
        """Toggle LLM provider between openrouter and zai (p key)."""
        pf = self.settings.provider_file
        current = pf.read_text().strip().lower() if pf.exists() else "openrouter"
        new_provider = "zai" if current == "openrouter" else "openrouter"
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text(new_provider)
        self.query_one(ControlsBar).update_status(f"provider: {new_provider}")

    def action_text_input(self) -> None:
        """Enter text injection mode (t key)."""
        self.query_one(ControlsBar).show_input()

    # -----------------------------------------------------------------------
    # Column resize actions
    # -----------------------------------------------------------------------

    def action_shrink_left(self) -> None:
        """Shrink activity panel width (left arrow)."""
        # TODO: Implement column width cycling in US-009
        self.query_one(ControlsBar).update_status("shrink panel")

    def action_grow_left(self) -> None:
        """Grow activity panel width (right arrow)."""
        # TODO: Implement column width cycling in US-009
        self.query_one(ControlsBar).update_status("grow panel")
