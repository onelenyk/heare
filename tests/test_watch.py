"""Tests for dashboard helpers in src/watch.py."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.config import Settings, Mode
from src.storage import SCHEMA
from src.watch.data import (
    counts,
    current_mode,
    daemon_status,
    fmt_time,
    open_db,
    truncate,
)
# Legacy functions for tests that still use them
from src.watch import (
    _activity_table,
    _bot_table,
    _did_table,
    _you_table,
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
    assert len(parts[0]) == 2
    assert len(parts[1]) == 2
    assert len(parts[2]) == 2


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
        db_path = Path(tmp) / "heare.db"
        _create_schema(db_path)
        con = sqlite3.connect(str(db_path))
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
# Helper to flatten Rich panels for testing
# ---------------------------------------------------------------------------

def _rich_text(panel) -> str:
    """Flatten a Rich Panel into plain text for substring assertions."""
    from rich.console import Console as _Console

    console = _Console(width=200, record=True, force_terminal=False, color_system=None)
    console.print(panel)
    return console.export_text()


def test_watch_cli_default_interval_is_half_second() -> None:
    """RT-004: watch CLI must default to 0.5s refresh so the dashboard feels
    realtime."""
    from src.main import build_parser

    parser = build_parser()
    args = parser.parse_args(["watch"])
    assert args.interval == 0.5


# ---------------------------------------------------------------------------
# Pipecat-native: 2-column body (You / Heare did) + Recent Activity panel
# ---------------------------------------------------------------------------


def _seed_pipecat_native_sample(db_path: Path) -> None:
    """Populate transcripts / actions with one turn worth of rows."""
    con = sqlite3.connect(str(db_path))
    con.executescript(SCHEMA)
    # transcript
    con.execute(
        "INSERT INTO transcripts (ts, text, mode) VALUES (1.0, 'запусти echo hi', 'ambient')"
    )
    # action row with new schema (intent_id, tool, args)
    con.execute(
        "INSERT INTO actions (ts, intent_id, tool, args, status, result_summary)"
        " VALUES (2.0, 'intent_123', 'bash', 'echo hi', 'ok', 'ran: echo hi')"
    )
    con.commit()
    con.close()


def test_you_table_shows_user_transcripts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "heare.db"
        _seed_pipecat_native_sample(db_path)
        con = sqlite3.connect(str(db_path))
        try:
            table = _you_table(con)
        finally:
            con.close()

    text = _rich_text(table)
    assert "🧑 You" in text
    assert "запусти echo hi" in text
    # The "who" column is always present; rows without speaker_id show "unknown".
    assert "who" in text
    assert "unknown" in text


def test_you_table_shows_speaker_labels() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "heare.db"
        _create_schema(db_path)
        con = sqlite3.connect(str(db_path))
        try:
            con.execute(
                "INSERT INTO transcripts (ts, text, mode, speaker_id)"
                " VALUES (?, ?, ?, ?)",
                (1000.0, "привіт", "ambient", "owner"),
            )
            con.execute(
                "INSERT INTO transcripts (ts, text, mode, speaker_id)"
                " VALUES (?, ?, ?, ?)",
                (1001.0, "я alice", "ambient", "guest_01"),
            )
            con.commit()
            table = _you_table(con, labels={"owner": "owner", "guest_01": "Alice"})
        finally:
            con.close()

    text = _rich_text(table)
    assert "owner" in text
    assert "Alice" in text
    assert "guest_01" not in text  # gallery label wins over raw id


def test_did_table_shows_tool_and_args() -> None:
    """Pipecat-native: 'Heare did' column must show status + tool + args + result_summary."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "heare.db"
        _seed_pipecat_native_sample(db_path)
        con = sqlite3.connect(str(db_path))
        try:
            table = _did_table(con)
        finally:
            con.close()

    text = _rich_text(table)
    assert "Heare did" in text or "⚙️" in text
    assert "ok" in text  # status
    assert "bash" in text  # tool column
    assert "echo hi" in text  # args column
    assert "ran: echo hi" in text  # result_summary


def test_activity_table_merges_transcripts_and_actions() -> None:
    """Recent Activity panel must show transcripts and actions in chronological order."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "heare.db"
        _create_schema(db_path)
        con_rw = sqlite3.connect(str(db_path))
        base = 1700000000.0
        # Add 3 transcripts
        for i in range(3):
            con_rw.execute(
                "INSERT INTO transcripts (ts, text, mode) VALUES (?, ?, ?)",
                (base + i, f"transcript {i}", "ambient"),
            )
        # Add 3 actions
        for i in range(3):
            con_rw.execute(
                "INSERT INTO actions (ts, intent_id, tool, args, status, result_summary) VALUES (?, ?, ?, ?, ?, ?)",
                (base + i + 0.5, f"intent_{i}", "bash", f"echo {i}", "ok", f"ran {i}"),
            )
        con_rw.commit()
        con_rw.close()

        con = sqlite3.connect(str(db_path))
        try:
            table = _activity_table(con)
        finally:
            con.close()

    text = _rich_text(table)
    assert "Recent Activity" in text or "📋" in text
    # Should show both transcripts and actions
    assert "transcript 0" in text
    assert "echo 0" in text
    # Should show type labels
    assert "You" in text or "🧑" in text
    assert "Did" in text or "⚙️" in text


def test_build_layout_has_activity_and_three_body_columns() -> None:
    """Pipecat-native: layout has 'activity' and 3-column body (you / bot / did)."""
    from src.watch import _build_layout

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "heare.db"
        _seed_pipecat_native_sample(db_path)
        settings = _make_settings(tmp, db_path=db_path)
        layout = _build_layout(settings)

    def _names(layout_obj) -> set[str]:
        out = {layout_obj.name} if layout_obj.name else set()
        for child in getattr(layout_obj, "children", []) or []:
            out |= _names(child)
        return out

    names = _names(layout)
    assert "activity" in names
    assert {"you", "bot", "did"}.issubset(names)
    # Old panels should NOT exist
    assert "said" not in names
    assert "progress" not in names


def test_empty_tables_render_none_yet_placeholders() -> None:
    """An empty DB still renders the two columns with placeholder rows."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "heare.db"
        _create_schema(db_path)
        con = sqlite3.connect(str(db_path))
        try:
            you = _rich_text(_you_table(con))
            did = _rich_text(_did_table(con))
        finally:
            con.close()

    assert "none yet" in you.lower() or "no activity" in you.lower()
    assert "none yet" in did.lower() or "no activity" in did.lower()


# ---------------------------------------------------------------------------
# Tools panel
# ---------------------------------------------------------------------------


def test_tools_table_lists_every_enabled_registry_tool() -> None:
    """Dashboard's tool reference must mirror tool_registry.TOOLS exactly."""
    from src.tool_registry import TOOLS
    from src.watch import _tools_table

    rendered = _rich_text(_tools_table())
    for tool in TOOLS.values():
        if tool.enabled:
            assert tool.name in rendered, f"missing enabled tool: {tool.name}"


def test_tools_table_shows_execution_kind() -> None:
    """Execution column distinguishes direct vs claude vs workflow."""
    from src.watch import _tools_table

    rendered = _rich_text(_tools_table())
    assert "direct" in rendered
    assert "workflow" in rendered


# ---------------------------------------------------------------------------
# Bot response logging (BOTLOG-01 through BOTLOG-04)
# ---------------------------------------------------------------------------


def test_bot_table_renders_assistant_responses() -> None:
    """_bot_table should show transcripts where speaker_id='bot'."""

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "heare.db"
        _create_schema(db_path)
        con = sqlite3.connect(str(db_path))
        try:
            # Add a user transcript (should be filtered out)
            con.execute(
                "INSERT INTO transcripts (ts, text, mode, speaker_id) VALUES (?, ?, ?, ?)",
                (1000.0, "hello", "ambient", "owner"),
            )
            # Add bot responses (should be shown)
            con.execute(
                "INSERT INTO transcripts (ts, text, mode, speaker_id) VALUES (?, ?, ?, ?)",
                (1001.0, "Hello! How can I help?", "assistant", "bot"),
            )
            con.execute(
                "INSERT INTO transcripts (ts, text, mode, speaker_id) VALUES (?, ?, ?, ?)",
                (1002.0, "I can assist you with that.", "assistant", "bot"),
            )
            con.commit()
            table = _bot_table(con)
        finally:
            con.close()

    text = _rich_text(table)
    assert "Bot said" in text or "🗣️" in text
    assert "Hello! How can I help?" in text
    assert "I can assist you with that." in text
    # User transcript should NOT appear
    assert "hello" not in text


def test_bot_table_handles_empty_db() -> None:
    """_bot_table should show placeholder when no bot responses exist."""

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "heare.db"
        _create_schema(db_path)
        con = sqlite3.connect(str(db_path))
        try:
            table = _bot_table(con)
        finally:
            con.close()

    text = _rich_text(table)
    assert "none yet" in text.lower() or "no activity" in text.lower()


def test_you_table_filters_bot_responses() -> None:
    """_you_table should NOT show transcripts where speaker_id='bot'."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "heare.db"
        _create_schema(db_path)
        con = sqlite3.connect(str(db_path))
        try:
            # Add user transcript
            con.execute(
                "INSERT INTO transcripts (ts, text, mode, speaker_id) VALUES (?, ?, ?, ?)",
                (1000.0, "user message", "ambient", "owner"),
            )
            # Add bot response (should be filtered out)
            con.execute(
                "INSERT INTO transcripts (ts, text, mode, speaker_id) VALUES (?, ?, ?, ?)",
                (1001.0, "bot response", "assistant", "bot"),
            )
            con.commit()
            table = _you_table(con)
        finally:
            con.close()

    text = _rich_text(table)
    assert "user message" in text
    # Bot response should NOT appear in You table
    assert "bot response" not in text


def test_build_layout_has_three_column_body() -> None:
    """Layout should have 3-column body: you, bot, did."""
    from src.watch import _build_layout

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "heare.db"
        _create_schema(db_path)
        settings = _make_settings(tmp, db_path=db_path)
        layout = _build_layout(settings)

    def _names(layout_obj) -> set[str]:
        out = {layout_obj.name} if layout_obj.name else set()
        for child in getattr(layout_obj, "children", []) or []:
            out |= _names(child)
        return out

    names = _names(layout)
    assert {"you", "bot", "did"}.issubset(names)
    # Old panels should NOT exist
    assert "said" not in names
    assert "progress" not in names

