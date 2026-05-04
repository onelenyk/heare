"""HeareDashboard - Main Textual app for watch dashboard.

This module contains the main App class that assembles all widgets,
binds hotkeys, and manages the refresh loop.
"""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.css.query import NoMatches

from ..config import Settings
from ..mute_gate import toggle_input_mute, toggle_mute
from ..watch_controls import restart_daemon, start_daemon, stop_daemon
from . import models
from .data import fetch_dashboard_state
from .screens import ModelSelectScreen
from .widgets import ActivityTable, AIBar, ControlsBar, HeaderBar, LogTail, UsageBar


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
        Binding("o", "pick_model", "Pick model", show=False),
        Binding("q", "quit", "Quit", show=True),
        Binding("[", "shrink_left", "Shrink", show=False),
        Binding("]", "grow_left", "Grow", show=False),
        Binding("f", "refresh_now", "Refresh", show=True),
        Binding("ctrl+r", "respawn", "Respawn", show=False),
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
        self._activity_width_index = 2  # default to "full"
        self._width_presets = ("fit", "half", "full")

    def compose(self) -> ComposeResult:
        yield ActivityTable()
        yield LogTail()
        yield HeaderBar(self.settings)
        yield Horizontal(
            ControlsBar(self.settings),
            AIBar(self.settings),
            UsageBar(),
            id="bottom-bar",
        )

    def on_mount(self) -> None:
        """Called when app is mounted. Wire timers and initial state."""
        self._refresh_timer = self.set_interval(self.interval, self._refresh_data)
        # Defer first paint until the compose() children are actually mounted.
        self.call_after_refresh(self._apply_activity_width)
        self.call_after_refresh(self._refresh_data)

    # Daemon-control actions are blocked while the text-input widget is open,
    # so a stray "q" or "x" while typing doesn't quit the app or stop the daemon.
    _GUARDED_ACTIONS = frozenset({
        "start_daemon", "stop_daemon", "restart_daemon",
        "toggle_mute_bot", "toggle_mute_mic", "toggle_provider",
        "pick_model", "shrink_left", "grow_left", "quit",
        "refresh_now", "respawn",
    })

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in self._GUARDED_ACTIONS:
            try:
                if self.query_one(ControlsBar)._showing_input:
                    return False
            except Exception:
                pass
        return True

    def _refresh_data(self) -> None:
        """Fetch fresh data and update all widgets.

        Skips silently if widgets aren't mounted yet (early on_mount tick) or
        a modal screen has hidden them (e.g. during ModelSelectScreen).
        """
        try:
            header = self.query_one(HeaderBar)
            activity = self.query_one(ActivityTable)
            log_tail = self.query_one(LogTail)
            ai_bar = self.query_one(AIBar)
            usage_bar = self.query_one(UsageBar)
        except NoMatches:
            return

        snapshot = fetch_dashboard_state(self.settings)
        header.refresh_data(snapshot.header)
        activity.refresh_data(snapshot.activity_rows)
        log_tail.refresh_data(snapshot.log_lines)
        provider = snapshot.header.provider
        ai_bar.refresh_data(provider, models.read_current_model(self.settings, provider))
        usage_bar.refresh_data(snapshot.usage)

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

    def action_pick_model(self) -> None:
        """Open the model-select dialog (o key)."""
        provider = self.query_one(AIBar)._provider
        current = models.read_current_model(self.settings, provider)

        def _on_dismiss(picked: str | None) -> None:
            if picked:
                models.write_current_model(self.settings, picked)
                self.query_one(AIBar).refresh_data(provider, picked)
                self.query_one(ControlsBar).update_status(f"model: {picked}")

        self.push_screen(ModelSelectScreen(self.settings, provider, current), _on_dismiss)

    def action_refresh_now(self) -> None:
        """Force a full data refresh + repaint (f)."""
        self._refresh_data()
        self.refresh(layout=True, repaint=True)
        try:
            self.query_one(ControlsBar).update_status("refreshed")
        except NoMatches:
            pass

    def action_respawn(self) -> None:
        """Quit the dashboard and signal the caller to re-launch (Ctrl+R)."""
        self.exit("__respawn__")

    # -----------------------------------------------------------------------
    # Column resize actions
    # -----------------------------------------------------------------------

    def _apply_activity_width(self) -> None:
        """Toggle the activity-{fit,half,full} CSS class on ActivityTable."""
        try:
            activity = self.query_one(ActivityTable)
            controls = self.query_one(ControlsBar)
        except NoMatches:
            return
        new_preset = self._width_presets[self._activity_width_index]
        for preset in self._width_presets:
            activity.remove_class(f"activity-{preset}")
        activity.add_class(f"activity-{new_preset}")
        controls.update_status(f"activity panel: {new_preset}")

    def action_shrink_left(self) -> None:
        """Shrink activity panel width (left arrow)."""
        self._activity_width_index = (self._activity_width_index - 1) % len(self._width_presets)
        self._apply_activity_width()

    def action_grow_left(self) -> None:
        """Grow activity panel width (right arrow)."""
        self._activity_width_index = (self._activity_width_index + 1) % len(self._width_presets)
        self._apply_activity_width()
