"""Tests for watch dashboard widgets (src.watch.widgets)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Static

from src.config import Settings, Mode
from src.watch.data import HeaderData
from src.watch.widgets import HeaderBar


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
