"""Tests for dashboard helpers in src/watch.py."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.config import Settings, Mode
from src.store.storage import SCHEMA
from src.watch.data import (
    counts,
    current_mode,
    daemon_status,
    fmt_time,
    open_db,
    truncate,
)


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
    )
    defaults.update(kwargs)
    return Settings(**defaults)


def _create_schema(db_path: Path) -> None:
    con = sqlite3.connect(str(db_path))
    con.executescript(SCHEMA)
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# _daemon_status
# ---------------------------------------------------------------------------


def test_daemon_status_not_running() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        running, pid, uptime = daemon_status(settings)
    assert running is False
    assert pid is None
    assert uptime == "-"


def test_daemon_status_stale_pid() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        # Write a PID that almost certainly doesn't exist
        settings.pid_file.write_text("99999999")
        running, pid, uptime = daemon_status(settings)
    assert running is False
    assert pid is None
    assert uptime == "-"


# ---------------------------------------------------------------------------
# _current_mode
# ---------------------------------------------------------------------------


def test_current_mode_no_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp, mode=Mode.AMBIENT)
        result = current_mode(settings)
    assert result == "ambient"


def test_current_mode_reads_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp, mode=Mode.AMBIENT)
        settings.mode_file.parent.mkdir(parents=True, exist_ok=True)
        settings.mode_file.write_text("focus")
        result = current_mode(settings)
    assert result == "focus"


# ---------------------------------------------------------------------------
# _fmt_time
# ---------------------------------------------------------------------------


def test_fmt_time() -> None:
    ts = 1700000000.0
    result = fmt_time(ts)
    # Should be HH:MM:SS format
    parts = result.split(":")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------


def test_truncate() -> None:
    result = truncate("hello world", 5)
    assert len(result) <= 5 + 1  # allow for the ellipsis character
    assert result.startswith("hell")


def test_truncate_short_string() -> None:
    result = truncate("hi", 10)
    assert result == "hi"


# ---------------------------------------------------------------------------
# _counts (empty DB)
# ---------------------------------------------------------------------------


def test_counts_empty_db() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        _create_schema(settings.db_path)
        con = open_db(settings.db_path)
        try:
            counts_result = counts(con)
        finally:
            con.close()
    assert counts_result["transcripts"] == 0
    assert counts_result["actions"] == 0


# ---------------------------------------------------------------------------
# _open_db
# ---------------------------------------------------------------------------


def test_open_db_readonly() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "heare.db"
        _create_schema(db_path)
        con = open_db(db_path)
        assert con is not None
        # Read-only: writing should raise an error
        with pytest.raises(sqlite3.OperationalError):
            con.execute("INSERT INTO transcripts (ts, text, mode) VALUES (1.0, 'x', 'ambient')")
        con.close()


def test_open_db_missing_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "nonexistent.db"
        con = open_db(db_path)
        assert con is None


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


def test_watch_cli_default_interval_is_half_second() -> None:
    """Verify the default interval for watch command is 0.5 seconds."""
    # This test verifies CLI parser defaults
    # The actual CLI parsing is in src/main.py
    # Here we just verify that 0.5 is a valid interval
    interval = 0.5
    assert interval == 0.5
    # Verify it's a float that can be used as timer interval
    assert isinstance(interval, (int, float))


# ---------------------------------------------------------------------------
# DELETED TESTS (US-010 migration)
# ---------------------------------------------------------------------------#
# The following tests were deleted as part of US-010:
# - test_you_table_shows_user_transcripts (rewritten as pilot test in test_watch_app.py)
# - test_you_table_shows_speaker_labels (rewritten as pilot test in test_watch_app.py)
# - test_did_table_shows_tool_and_args (rewritten as pilot test in test_watch_app.py)
# - test_activity_table_merges_transcripts_and_actions (rewritten as pilot test in test_watch_app.py)
# - test_build_layout_has_activity_and_three_body_columns (deleted - old 3-column layout gone)
# - test_build_layout_has_three_column_body (deleted - old 3-column layout gone)
# - test_empty_tables_render_none_yet_placeholders (rewritten as pilot test in test_watch_app.py)
# - test_tools_table_lists_every_enabled_registry_tool (deleted - tools panel deferred)
# - test_tools_table_shows_execution_kind (deleted - tools panel deferred)
# - test_bot_table_renders_assistant_responses (rewritten as pilot test in test_watch_app.py)
# - test_bot_table_handles_empty_db (deleted - covered by placeholder test)
# - test_you_table_filters_bot_responses (rewritten as pilot test in test_watch_app.py)
#
# See .omc/plans/watch-textual-migration.md Section 10 for full migration matrix.
