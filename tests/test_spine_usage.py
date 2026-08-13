"""Tests for SpineUsage — the sync usage tracking wrapper."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from src.spine.usage import SpineUsage
from src.store.storage import SCHEMA


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Create a temporary database with the schema initialized."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    return db_path


def test_llm_inserts_with_cost(tmp_db: Path) -> None:
    """Test that llm() inserts a row with kind='llm', tokens, and cost > 0 for known model."""
    usage = SpineUsage(tmp_db)

    # Record a known model (claude-haiku-4-5 is in the pricing catalog with cost)
    usage.llm(
        model="claude-haiku-4-5",
        input_tokens=100,
        output_tokens=50,
        provider="zai",
    )

    # Query the database
    conn = sqlite3.connect(str(tmp_db))
    cursor = conn.execute(
        "SELECT kind, provider, model, input_tokens, output_tokens, cost_usd FROM usage_events"
    )
    rows = cursor.fetchall()
    conn.close()

    assert len(rows) == 1
    kind, provider, model, input_tokens, output_tokens, cost_usd = rows[0]
    assert kind == "llm"
    assert provider == "zai"
    assert model == "claude-haiku-4-5"
    assert input_tokens == 100
    assert output_tokens == 50
    assert cost_usd > 0  # Known model should have cost > 0
    usage.close()


def test_stt_stores_audio_seconds(tmp_db: Path) -> None:
    """Test that stt() stores audio_seconds correctly."""
    usage = SpineUsage(tmp_db)

    audio_duration = 5.25
    usage.stt(audio_seconds=audio_duration, model="whisper-large-v3", provider="groq")

    conn = sqlite3.connect(str(tmp_db))
    cursor = conn.execute(
        "SELECT kind, provider, model, audio_seconds, cost_usd FROM usage_events"
    )
    rows = cursor.fetchall()
    conn.close()

    assert len(rows) == 1
    kind, provider, model, audio_seconds, cost_usd = rows[0]
    assert kind == "stt"
    assert provider == "groq"
    assert model == "whisper-large-v3"
    assert audio_seconds == audio_duration
    assert cost_usd > 0  # Groq has a price per second
    usage.close()


def test_tts_stores_char_count_with_zero_cost(tmp_db: Path) -> None:
    """Test that tts() stores char_count and cost is 0.0 for edge TTS (free)."""
    usage = SpineUsage(tmp_db)

    char_count = 150
    usage.tts(char_count=char_count, provider="edge", model="edge-tts")

    conn = sqlite3.connect(str(tmp_db))
    cursor = conn.execute(
        "SELECT kind, provider, model, char_count, cost_usd FROM usage_events"
    )
    rows = cursor.fetchall()
    conn.close()

    assert len(rows) == 1
    kind, provider, model, stored_char_count, cost_usd = rows[0]
    assert kind == "tts"
    assert provider == "edge"
    assert model == "edge-tts"
    assert stored_char_count == char_count
    assert cost_usd == 0.0  # edge-tts is free
    usage.close()


def test_unknown_model_no_exception_cost_zero(tmp_db: Path) -> None:
    """Test that unknown model → row inserted with cost 0.0, no exception."""
    usage = SpineUsage(tmp_db)

    # Use a completely unknown model
    usage.llm(
        model="unknown-model-xyz",
        input_tokens=100,
        output_tokens=50,
        provider="unknown",
    )

    conn = sqlite3.connect(str(tmp_db))
    cursor = conn.execute(
        "SELECT kind, model, input_tokens, output_tokens, cost_usd FROM usage_events"
    )
    rows = cursor.fetchall()
    conn.close()

    assert len(rows) == 1
    kind, model, input_tokens, output_tokens, cost_usd = rows[0]
    assert kind == "llm"
    assert model == "unknown-model-xyz"
    assert input_tokens == 100
    assert output_tokens == 50
    assert cost_usd == 0.0  # Unknown model → 0.0
    usage.close()


def test_today_usd_excludes_old_rows(tmp_db: Path) -> None:
    """Test that today_usd() sums only today's rows and excludes old ones."""
    usage = SpineUsage(tmp_db)

    # Insert today's rows (using a model with pricing)
    usage.llm(model="claude-haiku-4-5", input_tokens=100, output_tokens=50, provider="zai")
    usage.llm(model="claude-haiku-4-5", input_tokens=200, output_tokens=100, provider="zai")

    # Manually insert an old row (yesterday)
    conn = sqlite3.connect(str(tmp_db))
    yesterday = time.time() - 86400  # 24 hours ago
    conn.execute(
        """
        INSERT INTO usage_events (ts, kind, provider, model, input_tokens, output_tokens, cost_usd)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (yesterday, "llm", "zai", "claude-haiku-4-5", 50, 25, 0.001),
    )
    conn.commit()
    conn.close()

    # Get today's sum
    today_sum = usage.today_usd()

    # Query directly to verify the old row wasn't counted
    conn = sqlite3.connect(str(tmp_db))
    cursor = conn.execute("SELECT SUM(cost_usd) FROM usage_events WHERE ts >= ?", (yesterday,))
    all_sum = cursor.fetchone()[0] or 0.0
    conn.close()

    # today_sum should be less than all_sum (proves old row excluded)
    assert today_sum < all_sum
    assert today_sum > 0  # But should still have today's rows
    usage.close()


def test_broken_db_path_no_exception(tmp_path: Path) -> None:
    """Test that a broken db path doesn't raise exceptions."""
    # Create a file where we expect a directory (to make mkdir fail)
    blocking_file = tmp_path / "blocking_file"
    blocking_file.touch()

    # Try to use a path inside the blocking file
    broken_path = blocking_file / "subdir" / "test.db"

    bad_usage = SpineUsage(broken_path)

    # These should not raise, even with broken DB
    bad_usage.llm(model="test", input_tokens=10, output_tokens=5)
    bad_usage.stt(audio_seconds=1.0)
    bad_usage.tts(char_count=100)

    result = bad_usage.today_usd()
    assert result == 0.0  # Should return 0.0 on error

    bad_usage.close()  # Should not raise
