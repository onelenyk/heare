"""HeareDashboard - Main Textual app for watch dashboard.

This module contains the main App class that assembles all widgets,
binds hotkeys, and manages the refresh loop.
"""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.css.query import NoMatches

from src.config import Settings
from src.pipeline.stages.mute_gate import toggle_input_mute, toggle_mute
from src.daemon.browser import ensure_debug_chrome, is_debug_reachable, list_chrome_profiles
from src.daemon.watch_controls import restart_daemon, start_daemon, stop_daemon
from src.pipeline.stages.cancel_flag_gate import request_cancel
from . import models
from .data import fetch_dashboard_state
from .screens import AudioDeviceSelectScreen, ChromeProfileSelectScreen, ModelSelectScreen, ToolingScreen
from .widgets import ActivityTable, AgentResponseBar, AIBar, ControlsBar, DisplayPanel, HeaderBar, LogTail, UsageBar, VoiceStateBar


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
        Binding("k", "cancel_bot", "Interrupt", show=True),
        Binding("a", "pick_audio_device", "Audio dev", show=True),
        Binding("t", "text_input", "Text inject", show=True),
        Binding("c", "toggle_select_mode", "Select", show=True),
        Binding("b", "launch_chrome", "Chrome (CDP)", show=True),
        Binding("l", "show_tools", "Tools", show=True),
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
        self._select_mode = False

    def compose(self) -> ComposeResult:
        yield AgentResponseBar()
        yield DisplayPanel()
        yield ActivityTable()
        yield LogTail()
        yield HeaderBar(self.settings)
        yield Horizontal(
            ControlsBar(self.settings),
            AIBar(self.settings),
            VoiceStateBar(),
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
        "refresh_now", "respawn", "launch_chrome", "show_tools",
        "toggle_select_mode", "cancel_bot", "pick_audio_device",
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
            voice_bar = self.query_one(VoiceStateBar)
            agent_resp_bar = self.query_one(AgentResponseBar)
            display_panel = self.query_one(DisplayPanel)
        except NoMatches:
            return

        snapshot = fetch_dashboard_state(self.settings)
        header.refresh_data(snapshot.header)
        activity.refresh_data(snapshot.activity_rows)
        log_tail.refresh_data(snapshot.log_lines)
        provider = snapshot.header.provider
        ai_bar.refresh_data(provider, models.read_current_model(self.settings, provider))
        usage_bar.refresh_data(snapshot.usage)
        voice_bar.refresh_data(snapshot.voice_state, snapshot.audio_event)
        agent_resp_bar.refresh_data(snapshot.agent_response)
        display_panel.refresh_data(snapshot.display)

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

    def action_cancel_bot(self) -> None:
        """Interrupt the bot immediately (k key).

        Touches the cancel flag file — the pipeline's cancel_flag_gate
        picks it up on the next frame and pushes an InterruptionFrame
        upstream, which stops TTS and cancels any in-flight tool calls.
        This bypasses VAD/STT entirely — works even when the bot's
        audio drowns out the mic.
        """
        request_cancel(self.settings.cancel_flag_file)
        try:
            self.query_one(ControlsBar).update_status("interrupted")
        except NoMatches:
            pass

    def action_pick_audio_device(self) -> None:
        """Open the audio device picker (a key)."""
        def _on_dismiss(msg: str | None) -> None:
            if msg:
                self.query_one(ControlsBar).update_status(msg)

        self.push_screen(AudioDeviceSelectScreen(self.settings), _on_dismiss)

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

    def action_launch_chrome(self) -> None:
        """Launch (or attach to) Chrome with the CDP debug port (b key).

        If ``localhost:9222`` already responds, reports already-attached.
        Otherwise opens a profile picker so the user names which profile to
        launch — naming is required because Chrome's profile picker silently
        drops ``--remote-debugging-port`` when the user picks a profile from
        it, breaking CDP attach.
        """
        if is_debug_reachable():
            self.query_one(ControlsBar).update_status(
                "chrome already attached on :9222"
            )
            return

        profiles = list_chrome_profiles()
        if not profiles:
            # No Chrome user-data dir — try a bare launch as a fallback.
            self.query_one(ControlsBar).update_status(ensure_debug_chrome())
            return

        def _on_dismiss(picked: str | None) -> None:
            if picked is None:
                self.query_one(ControlsBar).update_status("chrome launch cancelled")
                return
            msg = ensure_debug_chrome(profile_directory=picked)
            self.query_one(ControlsBar).update_status(msg)

        self.push_screen(ChromeProfileSelectScreen(profiles), _on_dismiss)

    def action_show_tools(self) -> None:
        """Open the available-tooling dialog (l key)."""
        self.push_screen(ToolingScreen(self.settings))

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

    def action_toggle_select_mode(self) -> None:
        """Toggle mouse passthrough for native text selection (c key).

        When select mode is ON, Textual stops capturing mouse events so
        the terminal handles them natively — you can click-drag to
        highlight text and copy via Cmd+C / right-click. Press ``c``
        again to re-enable widget interaction.
        """
        self._select_mode = not self._select_mode
        if self._select_mode:
            self.screen.capture_mouse = "none"
            self.screen.add_class("select-mode")
            # Disable terminal mouse reporting so the terminal emulator
            # handles selection natively instead of forwarding events to
            # the app. Without this, capture_mouse="none" only changes
            # Textual's internal routing — the terminal is still in
            # Application Mouse Mode.
            self._toggle_terminal_mouse(enable=False)
            msg = "select mode ON — drag to select, Cmd+C to copy"
        else:
            self.screen.capture_mouse = "all"
            self.screen.remove_class("select-mode")
            self._toggle_terminal_mouse(enable=True)
            msg = "select mode OFF — interactive"
        try:
            self.query_one(ControlsBar).update_status(msg)
        except NoMatches:
            pass

    def _toggle_terminal_mouse(self, enable: bool) -> None:
        """Enable or disable the terminal's Application Mouse Mode.

        Sends the DEC private mode sequences that control whether mouse
        events are forwarded to the application or handled natively by
        the terminal emulator for text selection.

        Uses the same escape sequences Textual's LinuxDriver uses in
        ``_enable_mouse_support`` / ``_disable_mouse_support``.
        """
        if self._driver is None:
            return
        try:
            if enable:
                self._driver.write("\x1b[?1000h\x1b[?1003h\x1b[?1015h\x1b[?1006h")
            else:
                self._driver.write("\x1b[?1000l\x1b[?1003l\x1b[?1015l\x1b[?1006l")
            self._driver.flush()
        except Exception:
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
