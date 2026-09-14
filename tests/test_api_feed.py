"""Tests for activity feed with closed conversations and reply times."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import pytest

from src.api import API
from src.store.storage import SCHEMA


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_state():
    state = MagicMock()
    state.snapshot.return_value = {"mode": "focus", "provider": "deepseek"}
    state.get_bool.return_value = False
    state.set = AsyncMock()
    state.set_bool = AsyncMock()
    state.get = MagicMock(return_value="")
    return state


@pytest.fixture
def mock_config(tmp_path):
    config = MagicMock()
    config.deepseek_api_key = "sk-test"
    config.zai_api_key = None
    config.opencode_api_key = None
    config.openrouter_api_key = None
    config.db_path = tmp_path / "state.db"
    config.log_dir = tmp_path / "logs"
    return config


@pytest.fixture
def api(mock_state, mock_config):
    return API(mock_state, mock_config)


def _mock_request(
    *, json_data: dict | None = None, query: dict | None = None
) -> MagicMock:
    req = MagicMock()
    if json_data is not None:
        req.json = AsyncMock(return_value=json_data)
    req.query = query if query is not None else {}
    return req


def _create_schema(db_path: Path) -> None:
    """Initialize database with schema."""
    con = sqlite3.connect(str(db_path))
    con.executescript(SCHEMA)
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# GET /activity with closed conversations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activity_includes_closed_conversation(
    api, mock_config, tmp_path
) -> None:
    """Closed conversations appear in activity feed with start_ts/end_ts/content."""
    db_path = tmp_path / "heare.db"
    _create_schema(db_path)
    mock_config.db_path = db_path

    # Create a closed conversation
    con = sqlite3.connect(db_path)
    now = time.time()
    con.execute(
        "INSERT INTO conversations (start_ts, end_ts, mode, summary) "
        "VALUES (?, ?, ?, ?)",
        (now - 100, now - 50, "focus", "We discussed the weather"),
    )
    con.commit()
    con.close()

    resp = await api._handle_activity(_mock_request(query={"limit": "10"}))
    rows = json.loads(resp.body)

    # Should have one row with kind 'closed'
    closed = [r for r in rows if r.get("type") == "closed"]
    assert len(closed) == 1
    assert closed[0]["id"] == "c1"
    assert closed[0]["content"] == "We discussed the weather"
    assert closed[0]["start_ts"] == now - 100
    assert closed[0]["end_ts"] == now - 50


@pytest.mark.asyncio
async def test_activity_excludes_open_conversation(api, mock_config, tmp_path) -> None:
    """Open conversations (end_ts IS NULL) do not appear in activity feed."""
    db_path = tmp_path / "heare.db"
    _create_schema(db_path)
    mock_config.db_path = db_path

    # Create an open conversation (end_ts IS NULL)
    con = sqlite3.connect(db_path)
    now = time.time()
    con.execute(
        "INSERT INTO conversations (start_ts, end_ts, mode, summary) "
        "VALUES (?, ?, ?, ?)",
        (now - 100, None, "focus", "Open conversation"),
    )
    con.commit()
    con.close()

    resp = await api._handle_activity(_mock_request(query={"limit": "10"}))
    rows = json.loads(resp.body)

    # Should have no rows (open conversation excluded)
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_activity_closed_with_empty_summary(api, mock_config, tmp_path) -> None:
    """Closed conversations with NULL or empty summary still appear."""
    db_path = tmp_path / "heare.db"
    _create_schema(db_path)
    mock_config.db_path = db_path

    con = sqlite3.connect(db_path)
    now = time.time()
    con.execute(
        "INSERT INTO conversations (start_ts, end_ts, mode, summary) "
        "VALUES (?, ?, ?, ?)",
        (now - 100, now - 50, "focus", None),
    )
    con.commit()
    con.close()

    resp = await api._handle_activity(_mock_request(query={"limit": "10"}))
    rows = json.loads(resp.body)

    closed = [r for r in rows if r.get("type") == "closed"]
    assert len(closed) == 1
    assert closed[0]["content"] == ""


@pytest.mark.asyncio
async def test_activity_all_rows_have_start_end_ts(api, mock_config, tmp_path) -> None:
    """All activity rows include start_ts and end_ts fields."""
    db_path = tmp_path / "heare.db"
    _create_schema(db_path)
    mock_config.db_path = db_path

    con = sqlite3.connect(db_path)
    now = time.time()

    # Add a transcript row
    con.execute(
        "INSERT INTO transcripts (ts, text, mode, agent_spoken, source) "
        "VALUES (?, ?, ?, ?, ?)",
        (now, "hello", "user", None, "voice"),
    )

    # Add an action row
    con.execute(
        "INSERT INTO actions (ts, status, tool, args) "
        "VALUES (?, ?, ?, ?)",
        (now + 1, "done", "bash", "ls"),
    )

    # Add a closed conversation
    con.execute(
        "INSERT INTO conversations (start_ts, end_ts, mode, summary) "
        "VALUES (?, ?, ?, ?)",
        (now - 100, now - 50, "focus", "summary"),
    )

    con.commit()
    con.close()

    resp = await api._handle_activity(_mock_request(query={"limit": "10"}))
    rows = json.loads(resp.body)

    # All rows should have start_ts and end_ts
    for row in rows:
        assert "start_ts" in row
        assert "end_ts" in row
        # Transcripts and actions have NULL for these
        if row["type"] in ("said", "did"):
            assert row["start_ts"] is None
            assert row["end_ts"] is None
        # Closed has actual values
        elif row["type"] == "closed":
            assert row["start_ts"] == now - 100
            assert row["end_ts"] == now - 50


# ---------------------------------------------------------------------------
# GET /state with reply_ms
# ---------------------------------------------------------------------------


def test_reply_ms_reads_last_12_times(api, mock_config, tmp_path) -> None:
    """reply_ms reads last 12 total_ms values from turns.jsonl."""
    mock_config.log_dir = tmp_path / "logs"
    mock_config.log_dir.mkdir()
    turns_file = mock_config.log_dir / "turns.jsonl"

    # Write 15 lines with various total_ms values
    lines = []
    for i in range(15):
        lines.append(
            json.dumps(
                {
                    "ts": time.time() + i,
                    "stt_ms": 100,
                    "think_ms": 200,
                    "speak_ms": 300,
                    "total_ms": 100 + i * 10,  # 100, 110, 120, ..., 240
                    "chars": 100,
                    "interrupted": False,
                    "dropped_junk": False,
                    "role": "assistant",
                }
            )
        )

    with open(turns_file, "w") as f:
        f.write("\n".join(lines))

    times = api._read_reply_times()
    # Should have last 12 values, oldest first
    # i=3->130, i=4->140, ..., i=14->240
    assert len(times) == 12
    assert times == [130, 140, 150, 160, 170, 180, 190, 200, 210, 220, 230, 240]


def test_reply_ms_skips_dropped_junk(api, mock_config, tmp_path) -> None:
    """reply_ms skips lines where dropped_junk is true."""
    mock_config.log_dir = tmp_path / "logs"
    mock_config.log_dir.mkdir()
    turns_file = mock_config.log_dir / "turns.jsonl"

    lines = []
    for i in range(10):
        lines.append(
            json.dumps(
                {
                    "ts": time.time() + i,
                    "total_ms": 100 + i * 10,
                    "dropped_junk": i % 3 == 0,  # Every 3rd is dropped
                    "role": "assistant",
                }
            )
        )

    with open(turns_file, "w") as f:
        f.write("\n".join(lines))

    times = api._read_reply_times()
    # Should skip lines where dropped_junk=true (i=0, 3, 6, 9)
    # Remaining: i=1(110), i=2(120), i=4(140), i=5(150), i=7(170), i=8(180)
    assert len(times) == 6
    assert times == [110, 120, 140, 150, 170, 180]


def test_reply_ms_skips_zero_total_ms(api, mock_config, tmp_path) -> None:
    """reply_ms skips lines where total_ms is 0."""
    mock_config.log_dir = tmp_path / "logs"
    mock_config.log_dir.mkdir()
    turns_file = mock_config.log_dir / "turns.jsonl"

    lines = [
        json.dumps({"ts": time.time(), "total_ms": 0, "dropped_junk": False}),
        json.dumps({"ts": time.time() + 1, "total_ms": 100, "dropped_junk": False}),
        json.dumps({"ts": time.time() + 2, "total_ms": 0, "dropped_junk": False}),
        json.dumps({"ts": time.time() + 3, "total_ms": 200, "dropped_junk": False}),
    ]

    with open(turns_file, "w") as f:
        f.write("\n".join(lines))

    times = api._read_reply_times()
    # Should only include 100 and 200
    assert times == [100, 200]


def test_reply_ms_returns_empty_when_file_missing(api, mock_config, tmp_path) -> None:
    """reply_ms returns [] when turns.jsonl is missing."""
    mock_config.log_dir = tmp_path / "logs"
    mock_config.log_dir.mkdir()
    # Don't create turns.jsonl

    times = api._read_reply_times()
    assert times == []


def test_reply_ms_survives_malformed_json(api, mock_config, tmp_path) -> None:
    """reply_ms skips malformed JSON lines."""
    mock_config.log_dir = tmp_path / "logs"
    mock_config.log_dir.mkdir()
    turns_file = mock_config.log_dir / "turns.jsonl"

    lines = [
        json.dumps({"ts": time.time(), "total_ms": 100, "dropped_junk": False}),
        "this is not json",
        "",
        "{ incomplete json",
        json.dumps({"ts": time.time() + 1, "total_ms": 200, "dropped_junk": False}),
    ]

    with open(turns_file, "w") as f:
        f.write("\n".join(lines))

    times = api._read_reply_times()
    # Should only get the valid lines: 100 and 200
    assert times == [100, 200]


def test_reply_ms_survives_missing_fields(api, mock_config, tmp_path) -> None:
    """reply_ms survives lines missing total_ms or dropped_junk."""
    mock_config.log_dir = tmp_path / "logs"
    mock_config.log_dir.mkdir()
    turns_file = mock_config.log_dir / "turns.jsonl"

    lines = [
        json.dumps({"ts": time.time(), "total_ms": 100}),  # Missing dropped_junk
        json.dumps({"ts": time.time() + 1, "dropped_junk": False}),  # Missing total_ms
        json.dumps({"ts": time.time() + 2, "total_ms": 200, "dropped_junk": False}),
    ]

    with open(turns_file, "w") as f:
        f.write("\n".join(lines))

    times = api._read_reply_times()
    # First line: dropped_junk defaults to False, included
    # Second line: total_ms defaults to 0, skipped
    # Third line: included
    assert times == [100, 200]


def test_reply_ms_never_raises(api, mock_config, tmp_path) -> None:
    """reply_ms never raises, even on permission errors or corrupt data."""
    # Set log_dir to a non-existent nested path
    mock_config.log_dir = Path("/nonexistent/deeply/nested/path")

    # Should return [] without raising
    times = api._read_reply_times()
    assert times == []


def test_reply_ms_orders_oldest_first(api, mock_config, tmp_path) -> None:
    """reply_ms returns times oldest first, not newest first."""
    mock_config.log_dir = tmp_path / "logs"
    mock_config.log_dir.mkdir()
    turns_file = mock_config.log_dir / "turns.jsonl"

    # Write in chronological order (oldest first in file)
    lines = []
    for i in range(1, 6):
        lines.append(
            json.dumps({"ts": time.time() + i, "total_ms": i * 100, "dropped_junk": False})
        )

    with open(turns_file, "w") as f:
        f.write("\n".join(lines))

    times = api._read_reply_times()
    # Should be in ascending order: 100, 200, 300, 400, 500
    assert times == [100, 200, 300, 400, 500]
