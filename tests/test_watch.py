"""Tests for dashboard helpers in src/watch.py."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.config import Settings, Mode
from src.watch import (
    _counts,
    _current_mode,
    _daemon_status,
    _fmt_time,
    _open_db,
    _truncate,
)
from src.storage import SCHEMA


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
        running, pid, uptime = _daemon_status(settings)
    assert running is False
    assert pid is None
    assert uptime == "-"


def test_daemon_status_stale_pid() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        # Write a PID that almost certainly doesn't exist
        settings.pid_file.write_text("99999999")
        running, pid, uptime = _daemon_status(settings)
    assert running is False


# ---------------------------------------------------------------------------
# _current_mode
# ---------------------------------------------------------------------------

def test_current_mode_no_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp, mode=Mode.AMBIENT)
        result = _current_mode(settings)
    assert result == "ambient"


def test_current_mode_reads_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp, mode=Mode.AMBIENT)
        settings.mode_file.parent.mkdir(parents=True, exist_ok=True)
        settings.mode_file.write_text("focus")
        result = _current_mode(settings)
    assert result == "focus"


# ---------------------------------------------------------------------------
# _fmt_time
# ---------------------------------------------------------------------------

def test_fmt_time() -> None:
    ts = 1700000000.0
    result = _fmt_time(ts)
    # Should be HH:MM:SS format
    parts = result.split(":")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
    assert len(parts[0]) == 2
    assert len(parts[1]) == 2
    assert len(parts[2]) == 2


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------

def test_truncate() -> None:
    result = _truncate("hello world", 5)
    assert len(result) <= 5 + 1  # allow for the ellipsis character
    assert result.startswith("hell")


def test_truncate_short_string() -> None:
    result = _truncate("hi", 10)
    assert result == "hi"


# ---------------------------------------------------------------------------
# _counts (empty DB)
# ---------------------------------------------------------------------------

def test_counts_empty_db() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "heare.db"
        _create_schema(db_path)
        con = sqlite3.connect(str(db_path))
        try:
            counts = _counts(con)
        finally:
            con.close()
    assert counts["transcripts"] == 0
    assert counts["decisions"] == 0
    assert counts["actions"] == 0
    assert counts["heartbeats"] == 0


# ---------------------------------------------------------------------------
# _open_db
# ---------------------------------------------------------------------------

def test_open_db_readonly() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "heare.db"
        _create_schema(db_path)
        con = _open_db(db_path)
        assert con is not None
        # Read-only: writing should raise an error
        with pytest.raises(sqlite3.OperationalError):
            con.execute("INSERT INTO transcripts (ts, text, mode) VALUES (1.0, 'x', 'ambient')")
        con.close()


def test_open_db_missing_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "nonexistent.db"
        con = _open_db(db_path)
    assert con is None
