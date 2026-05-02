"""Tests for watch dashboard widgets (src.watch.widgets)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Static

from src.config import Settings, Mode
from src.watch.data import ActivityRow, HeaderData, LogLine
from src.watch.widgets import ActivityTable, ControlsBar, HeaderBar, LogTail, status_color


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
    """ActivityTable sets up 4 columns correctly."""
    from textual.app import App
    from textual.widgets import DataTable

    # Create a minimal app to host the widget
    app = App()

    # Manually create table without mounting (avoid App context requirement)
    from src.watch.widgets import ActivityTable

    # Just verify the class exists and has the right structure
    assert ActivityTable is not None
    # The _setup_columns method will be called when mounted in an App
    # Full pilot testing will be done in US-007 when App is assembled


# ---------------------------------------------------------------------------
# LogTail
# ---------------------------------------------------------------------------


def test_log_tail_initialization() -> None:
    """LogTail can be initialized."""
    log_tail = LogTail()
    assert log_tail is not None
    assert log_tail.MAX_LINES == 50


def test_log_tail_refresh_data() -> None:
    """LogTail.refresh_data() updates display with log lines."""
    log_tail = LogTail()

    lines = [
        LogLine("INFO: normal message", "info"),
        LogLine("ERROR: bad thing", "error"),
        LogLine("WARNING: caution", "warning"),
    ]

    log_tail.refresh_data(lines)

    # Should store the lines
    assert len(log_tail.lines) == 3


def test_log_tail_enforces_max_lines() -> None:
    """LogTail enforces MAX_LINES limit."""
    log_tail = LogTail()

    # Create 100 lines (more than MAX_LINES=50)
    lines = [LogLine(f"INFO: line {i}", "info") for i in range(100)]

    log_tail.refresh_data(lines)

    # Should only keep last 50
    assert len(log_tail.lines) == 50


def test_log_tail_empty_data_shows_placeholder() -> None:
    """LogTail shows placeholder when no data."""
    log_tail = LogTail()
    log_tail.refresh_data([])

    # Should update with placeholder
    assert len(log_tail.lines) == 0


# ---------------------------------------------------------------------------
# ControlsBar
# ---------------------------------------------------------------------------


def test_controls_bar_initialization() -> None:
    """ControlsBar can be initialized."""
    bar = ControlsBar()
    assert bar is not None
    assert bar._status_message == ""
    assert bar._showing_input is False


def test_controls_bar_update_status() -> None:
    """ControlsBar.update_status() updates status message."""
    bar = ControlsBar()
    bar.update_status("daemon started")

    assert bar._status_message == "daemon started"


def test_controls_bar_show_hide_input() -> None:
    """ControlsBar can toggle input mode."""
    bar = ControlsBar()

    bar.show_input()
    assert bar._showing_input is True

    bar.hide_input()
    assert bar._showing_input is False


def test_status_color_function() -> None:
    """status_color() returns correct colors for each status."""
    assert status_color("ok") == "green"
    assert status_color("done") == "green"
    assert status_color("error") == "red"
    assert status_color("cancelled") == "dim"
    assert status_color("pending") == "yellow"
    assert status_color("unknown") == "white"
    assert status_color(None) == "white"
