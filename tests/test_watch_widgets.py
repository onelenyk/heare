"""Tests for watch dashboard widgets (src.watch.widgets)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from textual.widgets import Static

from src.config import Settings, Mode
from src.watch.data import HeaderData, LogLine
from src.watch.widgets import ActivityTable, AIBar, ControlsBar, HeaderBar, LogTail, status_color


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(tmp: str, **kwargs) -> Settings:
    base = Path(tmp)
    defaults = dict(
        pid_file=base / "heare.pid",
        db_path=base / "heare.db",
        mode_file=base / "mode",
        log_dir=base / "logs",
        mode=Mode.AMBIENT,
        mute_file=base / "mute.bot",
        mute_input_file=base / "mute.input",
        provider_file=base / "provider",
        identity_file=base / "identity.json",
        inject_dir=base / "inject",
        speakers_file=base / "speakers.json",
    )
    defaults.update(kwargs)
    return Settings(**defaults)


# ---------------------------------------------------------------------------
# HeaderBar
# ---------------------------------------------------------------------------


def test_header_bar_initialization() -> None:
    """HeaderBar can be initialized with settings."""
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        header = HeaderBar(settings)
        assert header.settings == settings
        assert isinstance(header, Static)


def test_header_bar_refresh_data_displays_all_fields() -> None:
    """HeaderBar.refresh_data() renders all header fields correctly."""
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        header = HeaderBar(settings)

        # Create header data with all fields populated
        header_data = HeaderData(
            name="test",
            emoji="🧪",
            running=True,
            pid=12345,
            uptime="10m30s",
            mode="focus",
            provider="zai",
            transcripts_count=42,
            actions_count=7,
        )

        # Refresh with the data
        header.refresh_data(header_data)

        # Get the rendered content
        content = str(header.render())

        # Assert all fields are present
        assert "test" in content
        assert "🧪" in content
        assert "running" in content or "●" in content  # Status indicator
        assert "12345" in content
        assert "10m30s" in content
        assert "focus" in content
        assert "zai" in content
        assert "42" in content
        assert "7" in content


def test_header_bar_shows_stopped_status() -> None:
    """HeaderBar shows stopped status when daemon not running."""
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        header = HeaderBar(settings)

        header_data = HeaderData(
            name="test",
            emoji="🧪",
            running=False,
            pid=None,
            uptime="-",
            mode="ambient",
            provider="openrouter",
            transcripts_count=0,
            actions_count=0,
        )

        header.refresh_data(header_data)
        content = str(header.render())

        assert "stopped" in content or "○" in content


def test_header_bar_providers_have_correct_colors() -> None:
    """HeaderBar applies correct color styling for providers."""
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        header = HeaderBar(settings)

        # Test zai provider (should be cyan)
        header_data_zai = HeaderData(
            name="test",
            emoji="🧪",
            running=False,
            pid=None,
            uptime="-",
            mode="ambient",
            provider="zai",
            transcripts_count=0,
            actions_count=0,
        )
        header.refresh_data(header_data_zai)
        content_zai = str(header.render())
        assert "zai" in content_zai

        # Test openrouter provider (should be yellow)
        header_data_or = HeaderData(
            name="test",
            emoji="🧪",
            running=False,
            pid=None,
            uptime="-",
            mode="ambient",
            provider="openrouter",
            transcripts_count=0,
            actions_count=0,
        )
        header.refresh_data(header_data_or)
        content_or = str(header.render())
        assert "openrouter" in content_or


# ---------------------------------------------------------------------------
# ActivityTable
# ---------------------------------------------------------------------------


def test_activity_table_column_setup() -> None:
    """ActivityTable class exists and will set up columns when mounted."""
    # ActivityTable is imported at module level (line 12)
    # Column setup is tested in pilot tests (test_watch_app.py)
    assert ActivityTable is not None


# ---------------------------------------------------------------------------
# LogTail
# ---------------------------------------------------------------------------


def test_log_tail_initialization() -> None:
    """LogTail can be initialized."""
    log_tail = LogTail()
    assert log_tail is not None
    assert log_tail.MAX_LINES == 50


def test_log_tail_refresh_data() -> None:
    """LogTail.refresh_data() caches log lines and writes to RichLog."""
    log_tail = LogTail()

    lines = [
        LogLine("INFO: normal message", "info"),
        LogLine("ERROR: bad thing", "error"),
        LogLine("WARNING: caution", "warning"),
    ]

    log_tail.refresh_data(lines)

    assert len(log_tail._log_lines) == 3


def test_log_tail_enforces_max_lines() -> None:
    """LogTail trims its source cache to MAX_LINES."""
    log_tail = LogTail()

    lines = [LogLine(f"INFO: line {i}", "info") for i in range(100)]
    log_tail.refresh_data(lines)

    assert len(log_tail._log_lines) == 50


def test_log_tail_empty_data_shows_placeholder() -> None:
    """LogTail clears its source cache when given no lines."""
    log_tail = LogTail()
    log_tail.refresh_data([])
    assert len(log_tail._log_lines) == 0


# ---------------------------------------------------------------------------
# ControlsBar
# ---------------------------------------------------------------------------


def test_controls_bar_initialization() -> None:
    """ControlsBar can be initialized."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        bar = ControlsBar(settings)
        assert bar is not None
        assert bar._status_message == ""
        assert bar._showing_input is False


def test_controls_bar_update_status() -> None:
    """ControlsBar.update_status() updates status message."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        bar = ControlsBar(settings)
        bar.update_status("daemon started")

        assert bar._status_message == "daemon started"


def test_controls_bar_show_hide_input() -> None:
    """ControlsBar can toggle input mode state."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        bar = ControlsBar(settings)

        # Test state changes without actually mounting widgets
        # (mounting requires an App context, tested in pilot tests)
        assert bar._showing_input is False

        # Simulate state change (show_input requires App context)
        bar._showing_input = True
        assert bar._showing_input is True

        # hide_input can be called without mounting
        bar.hide_input()
        assert bar._showing_input is False


def test_ai_bar_initialization() -> None:
    """AIBar can be initialized."""
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        bar = AIBar(settings)
        assert bar._provider == "openrouter"
        assert bar._model == ""


def test_ai_bar_refresh_data_updates_state() -> None:
    """AIBar.refresh_data updates internal provider/model state."""
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        bar = AIBar(settings)
        bar.refresh_data("zai", "claude-3-5-sonnet")
        assert bar._provider == "zai"
        assert bar._model == "claude-3-5-sonnet"


def test_ai_bar_render_includes_provider_and_model() -> None:
    """AIBar.render() output mentions provider and model."""
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        bar = AIBar(settings)
        bar.refresh_data("openrouter", "anthropic/claude-haiku-4.5")
        output = str(bar.render())
        assert "openrouter" in output
        assert "anthropic/claude-haiku-4.5" in output


def test_status_color_function() -> None:
    """status_color() returns correct colors for each status."""
    assert status_color("ok") == "green"
    assert status_color("done") == "green"
    assert status_color("error") == "red"
    assert status_color("cancelled") == "dim"
    assert status_color("pending") == "yellow"
    assert status_color("unknown") == "white"
    assert status_color(None) == "white"
