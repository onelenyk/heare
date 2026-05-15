"""Tests for HeareDashboard App (src.watch.app)."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from src.config import Settings, Mode
from src.store.storage import SCHEMA
from src.watch.app import HeareDashboard
from src.watch import run_watch


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
    from src.store.storage import SCHEMA
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
    from src.store.storage import SCHEMA

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
            await pilot.pause()  # let deferred _refresh_data fire
            from src.watch.widgets import ActivityTable

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


# ---------------------------------------------------------------------------
# run_watch() tests
# ---------------------------------------------------------------------------


def test_once_mode_outputs_to_stdout(capsys: pytest.fixture) -> None:
    """once=True must bypass App, render via rich.Console to stdout, and exit."""
    import sqlite3

    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        (Path(tmp) / "logs").mkdir()

        # Create DB with schema and data
        con = sqlite3.connect(str(settings.db_path))
        con.executescript(SCHEMA)
        con.execute(
            "INSERT INTO transcripts (ts, text, mode, speaker_id) VALUES (?, ?, ?, ?)",
            (1700000000.0, "test message", "ambient", None),
        )
        con.commit()
        con.close()

        # Run once mode
        rc = run_watch(settings, interval=0.5, once=True)

        # Should exit successfully
        assert rc == 0

        # Check stdout has output
        captured = capsys.readouterr()
        assert len(captured.out) > 100  # Non-trivial output
        assert "heare" in captured.out or "test" in captured.out


def test_legacy_env_var_routing() -> None:
    """HEARE_WATCH_LEGACY=1 must route to the old run_watch."""
    import os

    old_env = os.environ.get("HEARE_WATCH_LEGACY")
    try:
        os.environ["HEARE_WATCH_LEGACY"] = "1"

        # Force reimport
        import importlib
        import src.watch
        importlib.reload(src.watch)

        # Should have run_watch from legacy
        assert callable(src.watch.run_watch)
    finally:
        if old_env is None:
            os.environ.pop("HEARE_WATCH_LEGACY", None)
        else:
            os.environ["HEARE_WATCH_LEGACY"] = old_env
        # Reload to restore normal behavior
        importlib.reload(src.watch)


@pytest.mark.asyncio
async def test_daemon_hotkeys_suppressed_during_text_input(monkeypatch: Mock) -> None:
    """Pressing daemon-control keys while text-input is active is a no-op."""
    mock_start = MagicMock(return_value="daemon started")
    monkeypatch.setattr("src.watch.app.start_daemon", mock_start)

    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        (Path(tmp) / "logs").mkdir()

        async with HeareDashboard(settings=settings).run_test() as pilot:
            await pilot.press("t")  # enter input mode
            await pilot.press("s")  # would normally fire start_daemon
            mock_start.assert_not_called()


@pytest.mark.asyncio
async def test_pick_model_opens_modal() -> None:
    """Pressing o pushes ModelSelectScreen onto the screen stack."""
    from src.watch.screens import ModelSelectScreen

    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        (Path(tmp) / "logs").mkdir()

        async with HeareDashboard(settings=settings).run_test() as pilot:
            await pilot.press("o")
            assert isinstance(pilot.app.screen, ModelSelectScreen)


@pytest.mark.asyncio
async def test_select_model_writes_model_file() -> None:
    """Dismissing ModelSelectScreen with a model id writes it to model_file."""
    from src.watch import models

    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        (Path(tmp) / "logs").mkdir()

        async with HeareDashboard(settings=settings).run_test() as pilot:
            await pilot.press("o")
            pilot.app.screen.dismiss("anthropic/claude-haiku-4.5")
            await pilot.pause()
            assert models.read_current_model(settings, "openrouter") == "anthropic/claude-haiku-4.5"


@pytest.mark.asyncio
async def test_show_tools_opens_modal() -> None:
    """Pressing l pushes ToolingScreen onto the screen stack."""
    from src.watch.screens import ToolingScreen

    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        (Path(tmp) / "logs").mkdir()

        async with HeareDashboard(settings=settings).run_test() as pilot:
            await pilot.press("l")
            assert isinstance(pilot.app.screen, ToolingScreen)


@pytest.mark.asyncio
async def test_show_tools_suppressed_during_text_input() -> None:
    """Pressing l while text-input is active is a no-op."""
    from src.watch.screens import ToolingScreen

    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        (Path(tmp) / "logs").mkdir()

        async with HeareDashboard(settings=settings).run_test() as pilot:
            await pilot.press("t")  # enter input mode
            await pilot.press("l")
            assert not isinstance(pilot.app.screen, ToolingScreen)


@pytest.mark.asyncio
async def test_grow_action_cycles_activity_width_class() -> None:
    """action_grow_left swaps the activity-N CSS class on ActivityTable."""
    from src.watch.widgets import ActivityTable

    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        (Path(tmp) / "logs").mkdir()

        async with HeareDashboard(settings=settings).run_test() as pilot:
            await pilot.pause()  # let deferred _apply_activity_width fire
            activity = pilot.app.query_one(ActivityTable)
            before = {c for c in activity.classes if c.startswith("activity-")}
            assert before, "expected an activity-N class on mount"

            pilot.app.action_grow_left()
            await pilot.pause()
            after = {c for c in activity.classes if c.startswith("activity-")}
            assert len(after) == 1
            assert after != before
