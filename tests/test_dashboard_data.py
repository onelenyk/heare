"""Tests for dashboard data layer — DB access functions for the API."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from src.store.storage import SCHEMA
from src.dashboard_data import UsageData, fetch_usage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_schema(db_path: Path) -> None:
    con = sqlite3.connect(str(db_path))
    con.executescript(SCHEMA)
    con.commit()
    con.close()


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


# ---------------------------------------------------------------------------
# fetch_usage (USE-001)
# ---------------------------------------------------------------------------


def test_fetch_usage_none_connection_returns_zero() -> None:
    """``con=None`` (DB unavailable) should yield a zero snapshot, not
    raise — the dashboard can render before the daemon writes anything."""
    usage = fetch_usage(None)
    assert usage.llm_calls == 0
    assert usage.total_cost_usd == 0.0


def test_fetch_usage_missing_table_returns_zero(tmp_path: Path) -> None:
    """Pre-USE-001 DBs (no usage_events table) must not crash the watch
    UI. The OperationalError is caught and zero is returned."""
    db_path = tmp_path / "old.db"
    con = sqlite3.connect(str(db_path))
    con.executescript("CREATE TABLE meta (k TEXT, v TEXT);")
    con.commit()
    try:
        usage = fetch_usage(con)
        assert isinstance(usage, UsageData)
        assert usage.total_cost_usd == 0.0
        assert usage.llm_calls == 0
    finally:
        con.close()


def test_fetch_usage_aggregates_recorded_events(tmp_path: Path) -> None:
    db_path = tmp_path / "heare.db"
    _create_schema(db_path)
    con = sqlite3.connect(str(db_path))
    con.execute(
        """
        INSERT INTO usage_events
            (ts, kind, provider, model, input_tokens, output_tokens,
             audio_seconds, char_count, cost_usd)
        VALUES
            (1000.0, 'llm', 'deepseek', 'g/flash', 10000, 5000, NULL, NULL, 0.00225),
            (1001.0, 'llm', 'deepseek', 'g/flash',  2000, 1000, NULL, NULL, 0.00045),
            (1002.0, 'stt', 'groq',        NULL,        NULL, NULL,  60.0, NULL, 0.000667),
            (1003.0, 'tts', 'edge_tts',    NULL,        NULL, NULL,  NULL, 1500, 0.0)
        """,
    )
    con.commit()
    try:
        usage = fetch_usage(con)
    finally:
        con.close()

    assert usage.llm_calls == 2
    assert usage.llm_input_tokens == 12_000
    assert usage.llm_output_tokens == 6_000
    assert abs(usage.llm_cost_usd - 0.0027) < 1e-9
    assert usage.stt_calls == 1
    assert abs(usage.stt_audio_seconds - 60.0) < 1e-9
    assert usage.tts_calls == 1
    assert usage.tts_char_count == 1500
    assert usage.tts_cost_usd == 0.0
    expected_total = 0.00225 + 0.00045 + 0.000667
    assert abs(usage.total_cost_usd - expected_total) < 1e-9
