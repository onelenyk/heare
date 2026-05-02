"""Tests for HeareDashboard App (src.watch.app)."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from textual.app import App

from src.config import Settings, Mode
from src.watch.app import HeareDashboard


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
# App smoke tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_app_boots_without_db() -> None:
    """Dashboard starts even when no DB file exists."""
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        (Path(tmp) / "logs").mkdir()

        async with HeareDashboard(settings=settings).run_test() as pilot:
            # App should boot without errors
            assert pilot.app is not None
            assert pilot.app.title == "heare"


@pytest.mark.asyncio
async def test_app_boots_with_empty_db() -> None:
    """Dashboard starts with an empty (schema-only) DB."""
    from src.storage import SCHEMA
    import sqlite3

    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        (Path(tmp) / "logs").mkdir()

        # Create empty DB with schema
        con = sqlite3.connect(str(settings.db_path))
        con.executescript(SCHEMA)
        con.close()

        async with HeareDashboard(settings=settings).run_test() as pilot:
            assert pilot.app is not None


@pytest.mark.asyncio
async def test_app_boots_with_seeded_db() -> None:
    """Dashboard shows real data from a populated DB."""
    import sqlite3
    from src.storage import SCHEMA

    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        (Path(tmp) / "logs").mkdir()

        # Create DB with schema and seed data
        con = sqlite3.connect(str(settings.db_path))
        con.executescript(SCHEMA)
        con.execute(
            "INSERT INTO transcripts (ts, text, mode, speaker_id) VALUES (?, ?, ?, ?)",
            (1700000000.0, "test message", "ambient", None),
        )
        con.execute(
            "INSERT INTO actions (ts, status, tool, args) VALUES (?, ?, ?, ?)",
            (1700000001.0, "ok", "bash", "ls"),
        )
        con.commit()
        con.close()

        async with HeareDashboard(settings=settings).run_test() as pilot:
            assert pilot.app is not None
            # Verify widgets are mounted and have data
            from src.watch.widgets import ActivityTable, HeaderBar, LogTail

            activity = pilot.app.query_one(ActivityTable)
            assert activity.row_count >= 1  # Should have our test data


# ---------------------------------------------------------------------------
# Key binding tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quit_key_exits_app() -> None:
    """Pressing q key exits the app cleanly."""
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        (Path(tmp) / "logs").mkdir()

        app = HeareDashboard(settings=settings)
        async with app.run_test() as pilot:
            # Press q to quit
            await pilot.press("q")
            # App should exit (no assertion error means success)


@pytest.mark.asyncio
async def test_start_key_calls_start_daemon(monkeypatch: Mock) -> None:
    """Pressing s key calls start_daemon."""
    from src.watch_controls import start_daemon

    mock_start = MagicMock(return_value="daemon started")
    monkeypatch.setattr("src.watch.app.start_daemon", mock_start)

    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        (Path(tmp) / "logs").mkdir()

        async with HeareDashboard(settings=settings).run_test() as pilot:
            await pilot.press("s")
            # Verify start_daemon was called
            mock_start.assert_called_once()


@pytest.mark.asyncio
async def test_mute_bot_toggles_mute() -> None:
    """Pressing m key toggles bot mute."""
    from src.mute_gate import toggle_mute

    mock_toggle = MagicMock(return_value=True)

    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        (Path(tmp) / "logs").mkdir()

        async with HeareDashboard(settings=settings).run_test() as pilot:
            # Mock the toggle function
            import src.watch.app
            original_toggle = src.watch.app.toggle_mute
            src.watch.app.toggle_mute = mock_toggle

            await pilot.press("m")

            # Restore
            src.watch.app.toggle_mute = original_toggle

            # Verify toggle was called
            mock_toggle.assert_called_once()


@pytest.mark.asyncio
async def test_provider_toggle_works() -> None:
    """Pressing p key toggles provider file."""
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        (Path(tmp) / "logs").mkdir()
        settings.provider_file.parent.mkdir(parents=True, exist_ok=True)

        # Set initial provider
        settings.provider_file.write_text("openrouter")

        async with HeareDashboard(settings=settings).run_test() as pilot:
            await pilot.press("p")

            # Provider should have toggled to zai
            new_provider = settings.provider_file.read_text().strip()
            assert new_provider == "zai"
