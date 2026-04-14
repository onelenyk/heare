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


# ---------------------------------------------------------------------------
# RT-004: _progress_panel + layout rewire
# ---------------------------------------------------------------------------


def _rich_text(panel) -> str:
    """Flatten a Rich Panel into plain text for substring assertions."""
    from rich.console import Console as _Console

    console = _Console(width=200, record=True, force_terminal=False, color_system=None)
    console.print(panel)
    return console.export_text()


def test_progress_panel_handles_none_con() -> None:
    from src.watch import _progress_panel

    panel = _progress_panel(None)
    # Must not raise, must render the "no progress yet" placeholder
    text = _rich_text(panel)
    assert "no progress yet" in text


def test_progress_panel_renders_empty_when_no_events() -> None:
    from src.watch import _progress_panel

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "heare.db"
        _create_schema(db_path)
        con = sqlite3.connect(str(db_path))
        try:
            panel = _progress_panel(con)
        finally:
            con.close()
    text = _rich_text(panel)
    assert "no progress yet" in text


def test_progress_panel_renders_events_in_chronological_order() -> None:
    """Seed 5 events at strictly increasing ts, assert the rendered panel
    lists them in chronological order (oldest first, newest last)."""
    from src.watch import _progress_panel

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "heare.db"
        _create_schema(db_path)
        con_rw = sqlite3.connect(str(db_path))
        base = 1700000000.0
        kinds = [
            "decider.start",
            "decider.done",
            "action.armed",
            "action.executing",
            "action.done",
        ]
        for i, kind in enumerate(kinds):
            con_rw.execute(
                "INSERT INTO events (ts, kind, transcript_id, decision_id, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (base + i, kind, None, None, None),
            )
        con_rw.commit()
        con_rw.close()

        con = sqlite3.connect(str(db_path))
        try:
            panel = _progress_panel(con)
        finally:
            con.close()

    text = _rich_text(panel)
    # Each kind should appear, and the earlier ones should show up BEFORE
    # the later ones in the rendered text (chronological order).
    for k in kinds:
        assert k in text
    pos = [text.index(k) for k in kinds]
    assert pos == sorted(pos), f"events out of order: {pos}"


def test_progress_panel_color_codes_kind_families() -> None:
    """_progress_panel must color-code rows by kind family. We don't assert
    raw ANSI codes; we assert that the three families render as three
    distinct styled segments."""
    from src.watch import _progress_panel

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "heare.db"
        _create_schema(db_path)
        con_rw = sqlite3.connect(str(db_path))
        rows = [
            (1.0, "decider.start"),   # cyan
            (2.0, "action.error"),    # red
            (3.0, "state.listening"), # dim
        ]
        for ts, kind in rows:
            con_rw.execute(
                "INSERT INTO events (ts, kind, transcript_id, decision_id, payload_json) "
                "VALUES (?, ?, NULL, NULL, NULL)",
                (ts, kind),
            )
        con_rw.commit()
        con_rw.close()

        con = sqlite3.connect(str(db_path))
        try:
            panel = _progress_panel(con)
        finally:
            con.close()

    # The panel should have 3 Text children inside the Group renderable.
    from rich.console import Group as _Group

    assert isinstance(panel.renderable, _Group)
    text_lines = [r for r in panel.renderable.renderables if hasattr(r, "style")]
    styles = [str(t.style) for t in text_lines]
    # Each of the three kinds maps to a distinct style.
    assert "cyan" in styles
    assert "red" in styles
    assert "dim" in styles


def test_build_layout_includes_progress_and_log() -> None:
    """_build_layout must expose both 'progress' and 'log' named layouts
    plus the existing 'header' and 'body'."""
    from src.watch import _build_layout

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "heare.db"
        _create_schema(db_path)
        settings = _make_settings(tmp, db_path=db_path)
        layout = _build_layout(settings)

    # Walk layout children and confirm the expected names
    def _names(layout_obj) -> set[str]:
        out = {layout_obj.name} if layout_obj.name else set()
        for child in getattr(layout_obj, "children", []) or []:
            out |= _names(child)
        return out

    names = _names(layout)
    assert "header" in names
    assert "body" in names
    assert "progress" in names
    assert "log" in names


def test_watch_cli_default_interval_is_half_second() -> None:
    """RT-004: watch CLI must default to 0.5s refresh so the dashboard feels
    realtime."""
    from src.main import build_parser

    parser = build_parser()
    args = parser.parse_args(["watch"])
    assert args.interval == 0.5
