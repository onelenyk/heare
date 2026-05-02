"""Tests for watch dashboard data layer (src.watch.data)."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.config import Settings, Mode
from src.storage import SCHEMA
from src.watch.data import (
    ActivityRow,
    DashboardSnapshot,
    fetch_activity,
    fetch_dashboard_state,
    fmt_time,
    read_log_tail,
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
        mute_file=base / "mute.bot",
        mute_input_file=base / "mute.input",
        provider_file=base / "provider",
        identity_file=base / "identity.json",
        inject_dir=base / "inject",
        speakers_file=base / "speakers.json",
    )
    defaults.update(kwargs)
    return Settings(**defaults)


def _create_schema(db_path: Path) -> None:
    con = sqlite3.connect(str(db_path))
    con.executescript(SCHEMA)
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# fmt_time
# ---------------------------------------------------------------------------


def test_fmt_time() -> None:
    ts = 1700000000.0
    result = fmt_time(ts)
    # Should be HH:MM:SS format
    parts = result.split(":")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


# ---------------------------------------------------------------------------
# read_log_tail
# ---------------------------------------------------------------------------


def test_read_log_tail_empty_file(tmp_path: Path) -> None:
    log_file = tmp_path / "daemon.log"
    log_file.write_text("")
    lines = read_log_tail(log_file, lines=10)
    assert lines == []


def test_read_log_tail_detects_severity(tmp_path: Path) -> None:
    log_file = tmp_path / "daemon.log"
    log_file.write_text(
        """INFO: normal message
WARNING: something fishy
ERROR: bad thing happened
DEBUG: verbose detail
"""
    )
    lines = read_log_tail(log_file, lines=20)
    assert len(lines) == 4
    assert lines[0].severity == "info"
    assert lines[1].severity == "warning"
    assert lines[2].severity == "error"
    assert lines[3].severity == "default"


def test_read_log_tail_limits_lines(tmp_path: Path) -> None:
    log_file = tmp_path / "daemon.log"
    log_lines = [f"INFO: line {i}\n" for i in range(100)]
    log_file.write_text("".join(log_lines))
    lines = read_log_tail(log_file, lines=20)
    assert len(lines) == 20
    # Should get last 20 lines
    assert "line 99" in lines[-1].text


def test_read_log_tail_missing_file(tmp_path: Path) -> None:
    lines = read_log_tail(tmp_path / "nonexistent.log", lines=10)
    assert lines == []


# ---------------------------------------------------------------------------
# fetch_activity
# ---------------------------------------------------------------------------


def test_fetch_activity_empty_db(tmp_path: Path) -> None:
    db_path = tmp_path / "heare.db"
    _create_schema(db_path)
    con = sqlite3.connect(str(db_path))
    rows = fetch_activity(con, limit=50)
    con.close()
    assert rows == []


def test_fetch_activity_merges_transcripts_and_actions(tmp_path: Path) -> None:
    db_path = tmp_path / "heare.db"
    _create_schema(db_path)
    con = sqlite3.connect(str(db_path))

    # Insert transcript
    con.execute(
        "INSERT INTO transcripts (ts, text, mode, speaker_id) VALUES (?, ?, ?, ?)",
        (1700000000.0, "hello", "ambient", None),
    )

    # Insert action
    con.execute(
        "INSERT INTO actions (ts, status, tool, args) VALUES (?, ?, ?, ?)",
        (1700000001.0, "ok", "bash", "ls -la"),
    )

    con.commit()
    rows = fetch_activity(con, limit=50)
    con.close()

    assert len(rows) == 2
    # Action should be first (newer)
    assert rows[0].who == "bash"
    assert rows[0].type_ == "ok"
    assert rows[0].status == "ok"
    # Transcript should be second
    assert rows[1].who == "you"
    assert rows[1].type_ == "said"
    assert rows[1].status is None


def test_fetch_activity_includes_status_field(tmp_path: Path) -> None:
    """Verify UNION ALL query includes status from actions table."""
    db_path = tmp_path / "heare.db"
    _create_schema(db_path)
    con = sqlite3.connect(str(db_path))

    # Insert actions with different statuses
    con.execute(
        "INSERT INTO actions (ts, status, tool, args) VALUES (?, ?, ?, ?)",
        (1700000000.0, "error", "bash", "fail"),
    )
    con.execute(
        "INSERT INTO actions (ts, status, tool, args) VALUES (?, ?, ?, ?)",
        (1700000001.0, "pending", "web_search", "query"),
    )

    con.commit()
    rows = fetch_activity(con, limit=50)
    con.close()

    assert len(rows) == 2
    assert rows[0].status == "pending"  # newest
    assert rows[0].type_ == "pending"
    assert rows[1].status == "error"
    assert rows[1].type_ == "error"


# ---------------------------------------------------------------------------
# fetch_dashboard_state
# ---------------------------------------------------------------------------


def test_fetch_dashboard_state_returns_frozen_snapshot(tmp_path: Path) -> None:
    settings = _make_settings(str(tmp_path))
    (tmp_path / "logs").mkdir()

    # Create empty DB
    _create_schema(settings.db_path)

    snapshot = fetch_dashboard_state(settings)

    # Verify it's a DashboardSnapshot
    assert isinstance(snapshot, DashboardSnapshot)
    # Verify it's frozen (dataclass with frozen=True)
    with pytest.raises(Exception):  # FrozenInstanceError
        snapshot.header.name = "modified"


def test_fetch_dashboard_state_includes_header_data(tmp_path: Path) -> None:
    settings = _make_settings(str(tmp_path))
    (tmp_path / "logs").mkdir()
    _create_schema(settings.db_path)

    # Create identity - load_identity expects specific structure
    settings.identity_file.parent.mkdir(parents=True, exist_ok=True)
    # Skip identity test for now - default to "heare"/"🪶"

    # Set mode
    settings.mode_file.parent.mkdir(parents=True, exist_ok=True)
    settings.mode_file.write_text("focus")

    snapshot = fetch_dashboard_state(settings)

    # Default identity when file missing/invalid
    assert snapshot.header.name == "heare"
    assert snapshot.header.emoji == "🪶"
    assert snapshot.header.mode == "focus"
    assert snapshot.header.running is False
    assert snapshot.header.pid is None
    assert snapshot.header.transcripts_count == 0
    assert snapshot.header.actions_count == 0


def test_fetch_dashboard_state_reads_mute_states(tmp_path: Path) -> None:
    settings = _make_settings(str(tmp_path))
    (tmp_path / "logs").mkdir()
    _create_schema(settings.db_path)

    # Create mute files - is_input_muted checks if file exists, not content
    settings.mute_file.write_text("1")
    # Don't create mute_input_file, so is_input_muted should be False

    snapshot = fetch_dashboard_state(settings)

    assert snapshot.is_muted is True
    assert snapshot.is_input_muted is False


# ---------------------------------------------------------------------------
# DB index verification
# ---------------------------------------------------------------------------


def test_idx_actions_index_created(tmp_path: Path) -> None:
    """Verify idx_actions_ts index is created on DB init."""
    db_path = tmp_path / "heare.db"
    _create_schema(db_path)

    con = sqlite3.connect(str(db_path))
    cursor = con.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_actions_ts'"
    )
    result = cursor.fetchone()
    con.close()

    assert result is not None
    assert result[0] == "idx_actions_ts"
