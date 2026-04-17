"""Tests for database migrations.

Tests verify that migration scripts create the correct tables and can be
run multiple times safely (idempotent).
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def empty_db():
    """Create an empty temporary database for migration testing."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        # Create empty database with base schema (transcripts, decisions, etc.)
        conn = sqlite3.connect(db_path)
        # Create base tables from storage.py SCHEMA (without conversations/turns)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transcripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                text TEXT NOT NULL,
                mode TEXT NOT NULL,
                speaker_id TEXT,
                speaker_confidence REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                transcript_id INTEGER,
                type TEXT NOT NULL,
                confidence REAL,
                reason TEXT,
                reply TEXT,
                intent TEXT,
                action_json TEXT,
                FOREIGN KEY(transcript_id) REFERENCES transcripts(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                decision_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                result_summary TEXT,
                FOREIGN KEY(decision_id) REFERENCES decisions(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS heartbeats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                decided_to_speak INTEGER NOT NULL,
                reply TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                kind TEXT NOT NULL,
                transcript_id INTEGER,
                decision_id INTEGER,
                payload_json TEXT,
                FOREIGN KEY(transcript_id) REFERENCES transcripts(id),
                FOREIGN KEY(decision_id) REFERENCES decisions(id) ON DELETE SET NULL
            )
        """)
        conn.commit()
        conn.close()
        yield db_path


def read_migration_script() -> str:
    """Read the migration SQL script."""
    migration_path = Path(__file__).parent.parent / "migrations" / "01_add_conversations.sql"
    return migration_path.read_text()


def execute_migration(conn: sqlite3.Connection) -> None:
    """Execute the migration script on a connection.

    Handles ALTER TABLE failures gracefully for idempotent execution.
    """
    script = read_migration_script()
    # Split by semicolon to handle ALTER TABLE errors
    for statement in script.split(";"):
        statement = statement.strip()
        if statement:
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as e:
                # Ignore "duplicate column name" errors for ALTER TABLE
                if "duplicate column name" not in str(e).lower():
                    raise
    conn.commit()


def test_migration_creates_tables(empty_db: Path) -> None:
    """US-009: Verify migration creates conversations and turns tables."""
    conn = sqlite3.connect(empty_db)

    # Run migration
    execute_migration(conn)

    # Verify conversations table exists
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='conversations'"
    )
    assert cursor.fetchone() is not None, "conversations table should exist"

    # Verify turns table exists
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='turns'"
    )
    assert cursor.fetchone() is not None, "turns table should exist"

    # Verify conversations table schema
    cursor = conn.execute("PRAGMA table_info(conversations)")
    columns = {row[1] for row in cursor.fetchall()}
    expected_columns = {"id", "start_ts", "end_ts", "mode", "summary", "entity_map"}
    assert expected_columns.issubset(columns), (
        f"conversations table missing columns: {expected_columns - columns}"
    )

    # Verify turns table schema
    cursor = conn.execute("PRAGMA table_info(turns)")
    columns = {row[1] for row in cursor.fetchall()}
    expected_columns = {"id", "conversation_id", "start_ts", "end_ts", "aggregated_text", "utterance_count", "topic_tags"}
    assert expected_columns.issubset(columns), (
        f"turns table missing columns: {expected_columns - columns}"
    )

    # Verify transcripts table has turn_id column (ALTER TABLE)
    cursor = conn.execute("PRAGMA table_info(transcripts)")
    columns = {row[1] for row in cursor.fetchall()}
    assert "turn_id" in columns, "transcripts table should have turn_id column"

    # Verify indexes were created
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_conversations_%' OR name LIKE 'idx_turns_%' OR name LIKE 'idx_transcripts_turn'"
    )
    indexes = {row[0] for row in cursor.fetchall()}
    expected_indexes = {
        "idx_conversations_mode",
        "idx_conversations_active",
        "idx_turns_conversation",
        "idx_transcripts_turn",
    }
    assert expected_indexes.issubset(indexes), (
        f"Missing indexes: {expected_indexes - indexes}, got: {indexes}"
    )

    conn.close()


def test_migration_is_idempotent(empty_db: Path) -> None:
    """US-009: Verify running migration twice is safe (idempotent)."""
    conn = sqlite3.connect(empty_db)

    # Run migration first time
    execute_migration(conn)

    # Insert some test data
    conn.execute(
        "INSERT INTO conversations (start_ts, mode, summary) VALUES (1.0, 'ambient', 'Test')"
    )
    conn.execute(
        "INSERT INTO turns (conversation_id, start_ts, end_ts, aggregated_text, utterance_count) "
        "VALUES (1, 1.0, 2.0, 'Test turn', 1)"
    )
    conn.commit()

    # Run migration second time
    # This should not fail or lose data (CREATE IF NOT EXISTS is idempotent)
    execute_migration(conn)

    # Verify data is still there
    cursor = conn.execute("SELECT COUNT(*) FROM conversations")
    assert cursor.fetchone()[0] == 1, "Conversation data should persist"

    cursor = conn.execute("SELECT COUNT(*) FROM turns")
    assert cursor.fetchone()[0] == 1, "Turn data should persist"

    # Verify schema is still correct
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='conversations'"
    )
    assert cursor.fetchone() is not None, "conversations table should still exist"

    conn.close()


def test_migration_foreign_key_constraints(empty_db: Path) -> None:
    """Verify foreign key constraints are properly defined."""
    conn = sqlite3.connect(empty_db)

    # Run migration
    execute_migration(conn)

    # Insert a conversation
    conn.execute(
        "INSERT INTO conversations (start_ts, mode, summary) VALUES (1.0, 'ambient', 'Test')"
    )
    conn.commit()

    # Insert a turn with valid conversation_id (should succeed)
    conn.execute(
        "INSERT INTO turns (conversation_id, start_ts, end_ts, aggregated_text, utterance_count) "
        "VALUES (1, 1.0, 2.0, 'Test turn', 1)"
    )
    conn.commit()

    # Try to insert a turn with invalid conversation_id (should fail with FK error)
    # Note: SQLite doesn't enforce FKs by default, need to enable
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute(
            "INSERT INTO turns (conversation_id, start_ts, end_ts, aggregated_text, utterance_count) "
            "VALUES (999, 1.0, 2.0, 'Invalid turn', 1)"
        )
        conn.commit()
        assert False, "Foreign key constraint should have failed"
    except sqlite3.IntegrityError:
        # Expected - FK constraint enforced
        pass

    conn.close()


def test_migration_creates_active_conversation_index(empty_db: Path) -> None:
    """Verify the active conversation index is a partial index on end_ts=NULL."""
    conn = sqlite3.connect(empty_db)

    # Run migration
    execute_migration(conn)

    # Verify idx_conversations_active is a partial index
    cursor = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_conversations_active'"
    )
    result = cursor.fetchone()
    assert result is not None, "idx_conversations_active should exist"
    sql = result[0]
    assert "WHERE end_ts IS NULL" in sql, (
        f"idx_conversations_active should be a partial index: {sql}"
    )

    conn.close()
