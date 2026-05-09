"""Dashboard widgets for Textual watch TUI."""
from __future__ import annotations

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.css.query import NoMatches
from textual.widgets import DataTable, Input, RichLog, Static

from src.config import Settings
from src.pipeline.stages.text_injector import inject_text
from src.version import app_version
from .data import ActivityRow, AudioEventData, HeaderData, LogLine, UsageData, VoiceStateData, fmt_time


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
        self.border_title = "📡 status"

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

        # Line 2: transcripts actions chrome   <version>
        # Chrome shows green ● when extension is connected, grey ○ otherwise.
        line2 = Text.assemble(
            ("transcripts=", "dim"),
            (f"{header.transcripts_count}", "white"),
            ("  actions=", "dim"),
            (f"{header.actions_count}", "white"),
            ("  chrome=", "dim"),
            (("●", "bold green") if header.chrome_attached else ("○", "dim")),
            ("   ", ""),
            (app_version(), "dim italic"),
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
        super().__init__(zebra_stripes=True, cursor_type="row")
        self._setup_columns()
        self.border_title = "📋 activity"

    def _setup_columns(self) -> None:
        """Configure table columns."""
        self.add_column("time", width=8)
        self.add_column("WHO", width=12)
        self.add_column("TYPE", width=10)
        self.add_column("content", width=100)  # Fixed width, will truncate in refresh_data

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


class LogTail(RichLog):
    """Log tail widget showing last N lines from daemon.log.

    Inherits from Textual's RichLog for native scrollback. Displays log
    lines with severity-based coloring: ERROR=red, WARNING=yellow,
    INFO=cyan, other=default. RichLog's max_lines caps memory.
    """

    MAX_LINES = 50

    def __init__(self) -> None:
        super().__init__(max_lines=self.MAX_LINES, highlight=False, markup=False, wrap=False)
        self._log_lines: list[LogLine] = []
        self.border_title = "📜 daemon log"

    def refresh_data(self, lines: list[LogLine]) -> None:
        """Update log display from dashboard snapshot.

        Args:
            lines: Log lines from read_log_tail()
        """
        self._log_lines = lines[-self.MAX_LINES :] if len(lines) > self.MAX_LINES else lines
        self.clear()
        if not self._log_lines:
            self.write(Text("(no log lines yet)", style="dim italic"))
            return
        for line in self._log_lines:
            self.write(self._style_line(line))

    def _style_line(self, line: LogLine) -> Text:
        if line.severity == "error":
            return Text(line.text, style="red")
        if line.severity == "warning":
            return Text(line.text, style="yellow")
        if line.severity == "info":
            return Text(line.text, style="cyan")
        return Text(line.text)


class Section(Static):
    """A bordered hotkey-section panel rendered inside ControlsBar.

    The section title (e.g. "AGENT") is shown as the border-title; the body
    is a Rich `Text` of `key label` pairs.
    """

    def __init__(self, label: str, hotkeys: tuple[tuple[str, str], ...]) -> None:
        super().__init__()
        self.border_title = label
        self._hotkeys = hotkeys

    def render(self) -> Text:
        text = Text()
        for i, (key, name) in enumerate(self._hotkeys):
            if i > 0:
                text.append("  ", style="dim")
            text.append(key, style="bold cyan")
            text.append(" ", style="")
            text.append(name, style="white")
        return text


class ControlsBar(Container):
    """Control bar — outer bordered region holding three Section panels.

    Layout (top → bottom):
      • status line  (the last-action message, or blank)
      • horizontal row with three Sections: AGENT / MUTE / INPUT
      • extras line  ([] resize, f refresh, ^R respawn, q quit)
      • Input widget (mounted dynamically when text-input mode is active)
    """

    SECTIONS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
        ("AGENT",   (("s", "start"), ("x", "stop"), ("r", "restart"))),
        ("MUTE",    (("m", "bot"),   ("M", "mic"))),
        ("INPUT",   (("t", "text"),)),
    )
    EXTRAS: tuple[tuple[str, str], ...] = (
        ("[]", "resize"),
        ("f", "refresh"),
        ("^R", "respawn"),
        ("q", "quit"),
    )

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings
        self._status_message = ""
        self._input_widget: Input | None = None
        self._showing_input = False
        self.border_title = "🎛 controls"

    def compose(self) -> ComposeResult:
        yield Static(Text(""), id="status-line")
        with Horizontal(id="sections-row"):
            for label, hotkeys in self.SECTIONS:
                yield Section(label, hotkeys)
        yield Static(self._build_extras(), id="extras-line")

    def on_mount(self) -> None:
        self._refresh_status()

    def _build_extras(self) -> Text:
        text = Text()
        for i, (key, label) in enumerate(self.EXTRAS):
            if i > 0:
                text.append("   ", style="dim")
            text.append(key, style="bold cyan")
            text.append(" ", style="")
            text.append(label, style="dim white")
        return text

    def update_status(self, msg: str) -> None:
        """Update the status message display."""
        self._status_message = msg
        self._refresh_status()

    def _refresh_status(self) -> None:
        try:
            line = self.query_one("#status-line", Static)
        except NoMatches:
            return
        if self._showing_input:
            line.update(Text("⤷ awaiting input…", style="bold yellow"))
            return
        if self._status_message:
            text = Text()
            text.append("⤷ ", style="dim")
            text.append(self._status_message, style="bold yellow")
            line.update(text)
        else:
            line.update(Text(""))

    def show_input(self) -> None:
        """Show the Input widget and hide the section panels."""
        if self._showing_input:
            return
        self._showing_input = True
        self.add_class("input-mode")
        self._input_widget = Input(
            placeholder="Type a message — Enter to send, Esc to cancel",
        )
        self.mount(self._input_widget)
        self._input_widget.focus()
        self._refresh_status()

    def hide_input(self) -> None:
        if not self._showing_input:
            return
        self._showing_input = False
        self.remove_class("input-mode")
        if self._input_widget:
            self._input_widget.remove()
            self._input_widget = None
        self._refresh_status()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value
        if text:
            try:
                inject_text(self._settings.inject_dir, text)
                self.update_status(
                    f"injected: {text[:30]}{'...' if len(text) > 30 else ''}"
                )
            except Exception as e:
                self.update_status(f"error: {e}")
        self.hide_input()

    def on_key(self, event: events.Key) -> None:
        if self._showing_input and event.key == "escape":
            self.hide_input()
            event.stop()


class AIBar(Static):
    """AI configuration panel showing the active provider and model.

    Operator changes provider via `p` (cycles openrouter↔zai) and picks a
    model via `o` (opens ModelSelectScreen). The widget itself is read-only;
    the App writes to provider_file / model_file.
    """

    HOTKEYS: tuple[tuple[str, str], ...] = (
        ("p", "switch provider"),
        ("o", "pick model"),
    )

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings
        self._provider = "openrouter"
        self._model = ""
        self.border_title = "🤖 AI"

    def refresh_data(self, provider: str, model: str) -> None:
        self._provider = provider
        self._model = model
        self.update(self._build_text())

    def _build_text(self) -> Text:
        text = Text()
        prov_style = "cyan" if self._provider == "zai" else "yellow"
        text.append("provider  ", style="dim")
        text.append(self._provider, style=f"bold {prov_style}")
        text.append("\n")
        text.append("model     ", style="dim")
        text.append(self._model or "(default)", style="bold white")
        text.append("\n\n")
        for i, (key, label) in enumerate(self.HOTKEYS):
            if i > 0:
                text.append("   ", style="dim")
            text.append(key, style="bold cyan")
            text.append(" ", style="")
            text.append(label, style="white")
        return text

    def render(self) -> Text:
        return self._build_text()


class VoiceStateBar(Static):
    """Live voice-state panel: idle / listening / stt / result + last audio event.

    Reads ``DashboardSnapshot.voice_state`` (written by the pipeline's
    ``VoiceStateObserver`` to a JSON file) and ``DashboardSnapshot.audio_event``
    (written by ``src/audio_event/writer.py``). The "result" state auto-decays
    to idle after ``RESULT_TTL_S`` and event lines auto-decay after
    ``EVENT_TTL_S``, so the panel returns to a calm state without writer-side
    timers.
    """

    RESULT_TTL_S: float = 4.0
    EVENT_TTL_S: float = 5.0

    def __init__(self) -> None:
        super().__init__()
        self._voice = VoiceStateData(
            state="idle", since_ts=0.0, last_partial=None, last_final=None
        )
        self._event = AudioEventData(label=None, score=0.0, ts=0.0)
        self.border_title = "🎤 voice"

    def refresh_data(
        self, voice: VoiceStateData, audio_event: AudioEventData | None = None
    ) -> None:
        self._voice = voice
        if audio_event is not None:
            self._event = audio_event
        self.update(self._build_text())

    def _effective_state(self) -> str:
        import time

        if self._voice.state == "result":
            if time.time() - self._voice.since_ts > self.RESULT_TTL_S:
                return "idle"
        return self._voice.state

    def _build_text(self) -> Text:
        state = self._effective_state()
        labels = {
            "idle":      ("○ idle",      "dim"),
            "listening": ("● listening", "bold green"),
            "stt":       ("◐ transcribing", "bold yellow"),
            "result":    ("✓ result",    "bold cyan"),
        }
        label, style = labels.get(state, (state, "white"))
        text = Text()
        text.append(label, style=style)
        text.append("\n")

        if state == "stt" and self._voice.last_partial:
            text.append("partial  ", style="dim")
            text.append(
                self._voice.last_partial[:60],
                style="italic white",
            )
        elif state == "result" and self._voice.last_final:
            text.append("said     ", style="dim")
            text.append(self._voice.last_final[:60], style="white")
        elif state == "listening":
            text.append("(speak now)", style="dim italic")
        else:
            text.append("(no audio)", style="dim italic")

        if self._event.label is not None:
            import time

            if time.time() - self._event.ts < self.EVENT_TTL_S:
                text.append("\n")
                text.append("event    ", style="dim")
                text.append(self._event.label, style="bold magenta")
                text.append(f" ({self._event.score:.2f})", style="dim magenta")
        return text

    def render(self) -> Text:
        return self._build_text()


class UsageBar(Static):
    """Running token / cost ledger.

    Reads from ``usage_events`` via the dashboard snapshot. Costs are
    cumulative since the table was created — not just the current
    session — so a glance shows lifetime spend on the configured
    models. Unknown-model calls render with ``?`` so a missing entry
    in :mod:`src.agent.llm.pricing` doesn't silently zero the bill.
    """

    def __init__(self) -> None:
        super().__init__()
        self._usage = UsageData(
            llm_calls=0, llm_input_tokens=0, llm_output_tokens=0, llm_cost_usd=0.0,
            stt_calls=0, stt_audio_seconds=0.0, stt_cost_usd=0.0,
            tts_calls=0, tts_char_count=0, tts_cost_usd=0.0,
            total_cost_usd=0.0,
        )
        self.border_title = "💰 usage"

    def refresh_data(self, usage: UsageData) -> None:
        self._usage = usage
        self.update(self._build_text())

    @staticmethod
    def _fmt_tokens(n: int) -> str:
        """``8412 -> '8.4k'``, ``500 -> '500'``. Keeps the panel width
        bounded so a long-running daemon's columns don't drift."""
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}k"
        return str(n)

    @staticmethod
    def _fmt_minutes(seconds: float) -> str:
        """Audio duration as ``M.m min`` for readability."""
        return f"{seconds / 60.0:.1f} min"

    @staticmethod
    def _fmt_cost(cost: float) -> str:
        """Costs are usually cents-of-dollars; show 4 decimals so a
        $0.0021 charge isn't rounded down to $0.00."""
        return f"${cost:.4f}"

    def _build_text(self) -> Text:
        u = self._usage
        text = Text()

        text.append("llm  ", style="dim")
        text.append(f"{u.llm_calls}", style="bold white")
        text.append(" calls, ", style="dim")
        text.append(self._fmt_tokens(u.llm_input_tokens), style="white")
        text.append(" in / ", style="dim")
        text.append(self._fmt_tokens(u.llm_output_tokens), style="white")
        text.append(" out  → ", style="dim")
        text.append(self._fmt_cost(u.llm_cost_usd), style="bold green")
        text.append("\n")

        text.append("stt  ", style="dim")
        text.append(f"{u.stt_calls}", style="bold white")
        text.append(" calls, ", style="dim")
        text.append(self._fmt_minutes(u.stt_audio_seconds), style="white")
        text.append("        → ", style="dim")
        text.append(self._fmt_cost(u.stt_cost_usd), style="bold green")
        text.append("\n")

        text.append("tts  ", style="dim")
        text.append(f"{u.tts_calls}", style="bold white")
        text.append(" calls", style="dim")
        text.append("                  → ", style="dim")
        if u.tts_cost_usd == 0.0 and u.tts_calls > 0:
            text.append("free", style="bold green")
        else:
            text.append(self._fmt_cost(u.tts_cost_usd), style="bold green")
        text.append("\n")

        text.append("─" * 40, style="dim")
        text.append("\n")
        text.append("total                         → ", style="dim")
        text.append(self._fmt_cost(u.total_cost_usd), style="bold yellow")
        return text

    def render(self) -> Text:
        return self._build_text()
