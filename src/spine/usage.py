"""One place where every paid call is counted — sync wrapper around usage_events.

The daemon records LLM/STT/TTS usage into the usage_events table so the
dashboard can show running token / cost totals. This module swallows all
DB errors and never breaks the conversation — accounting is best-effort.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

from src.agent.llm import pricing
from src.store.storage import SCHEMA


logger = logging.getLogger("heare.spine.usage")


class SpineUsage:
    """One place where every paid call is counted."""

    def __init__(self, db_path: str | Path) -> None:
        """Initialize the usage tracker with a DB path.

        Args:
            db_path: Path to the SQLite database. Will be created if it doesn't exist.
        """
        self.db_path = Path(db_path)
        self._db: Optional[sqlite3.Connection] = None
        self._ensure_db()

    def _ensure_db(self) -> None:
        """Initialize DB connection and schema if needed."""
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(self.db_path))
            # Enable foreign keys for referential integrity
            self._db.execute("PRAGMA foreign_keys=ON")
            # Initialize schema
            self._db.executescript(SCHEMA)
            self._db.commit()
        except Exception as e:
            logger.exception("Failed to initialize usage DB: %s", e)
            self._db = None

    def _get_db(self) -> Optional[sqlite3.Connection]:
        """Get DB connection, attempting to reconnect if closed."""
        if self._db is None:
            self._ensure_db()
        return self._db

    def llm(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        provider: str = "deepseek",
    ) -> None:
        """Record an LLM API call.

        Args:
            model: Model identifier (e.g., 'deepseek-chat').
            input_tokens: Number of input tokens used.
            output_tokens: Number of output tokens generated.
            provider: Provider name (default: 'deepseek').
        """
        try:
            # Compute cost using the pricing catalog
            cost = pricing.llm_cost(
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            # Unknown model → cost None → store as 0.0
            cost_usd = cost if cost is not None else 0.0

            db = self._get_db()
            if db is None:
                return

            db.execute(
                """
                INSERT INTO usage_events
                    (ts, kind, provider, model,
                     input_tokens, output_tokens, audio_seconds, char_count, cost_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    time.time(),
                    "llm",
                    provider,
                    model,
                    input_tokens,
                    output_tokens,
                    None,
                    None,
                    cost_usd,
                ),
            )
            db.commit()
        except Exception as e:
            logger.exception("Failed to record LLM usage: %s", e)

    def stt(
        self,
        audio_seconds: float,
        model: str = "whisper-large-v3",
        provider: str = "groq",
    ) -> None:
        """Record an STT (speech-to-text) API call.

        Args:
            audio_seconds: Duration of audio processed in seconds.
            model: Model identifier (default: 'whisper-large-v3').
            provider: Provider name (default: 'groq').
        """
        try:
            # Compute cost using the pricing catalog
            cost = pricing.stt_cost(provider=provider, audio_seconds=audio_seconds)
            # Unknown provider → cost None → store as 0.0
            cost_usd = cost if cost is not None else 0.0

            db = self._get_db()
            if db is None:
                return

            db.execute(
                """
                INSERT INTO usage_events
                    (ts, kind, provider, model,
                     input_tokens, output_tokens, audio_seconds, char_count, cost_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    time.time(),
                    "stt",
                    provider,
                    model,
                    None,
                    None,
                    audio_seconds,
                    None,
                    cost_usd,
                ),
            )
            db.commit()
        except Exception as e:
            logger.exception("Failed to record STT usage: %s", e)

    def tts(
        self,
        char_count: int,
        provider: str = "edge",
        model: str = "edge-tts",
    ) -> None:
        """Record a TTS (text-to-speech) API call.

        Args:
            char_count: Number of characters in the generated speech.
            provider: Provider name (default: 'edge', which is free).
            model: Model identifier (default: 'edge-tts').
        """
        try:
            # Compute cost using the pricing catalog
            cost = pricing.tts_cost(provider=provider, char_count=char_count)
            # Unknown provider or free (edge) → cost None → store as 0.0
            cost_usd = cost if cost is not None else 0.0

            db = self._get_db()
            if db is None:
                return

            db.execute(
                """
                INSERT INTO usage_events
                    (ts, kind, provider, model,
                     input_tokens, output_tokens, audio_seconds, char_count, cost_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    time.time(),
                    "tts",
                    provider,
                    model,
                    None,
                    None,
                    None,
                    char_count,
                    cost_usd,
                ),
            )
            db.commit()
        except Exception as e:
            logger.exception("Failed to record TTS usage: %s", e)

    def today_usd(self) -> float:
        """Sum of cost_usd since local midnight — for a spoken answer to
        'скільки ти сьогодні напрацював?' (how much did you earn today?).

        Returns:
            Total cost in USD for calls made since local midnight today.
            Returns 0.0 if the DB is unavailable or there are no rows.
        """
        try:
            db = self._get_db()
            if db is None:
                return 0.0

            # Compute today's midnight in seconds since epoch
            now = time.time()
            # Convert to local time struct, set to midnight, convert back
            import time as time_module
            local_time = time_module.localtime(now)
            midnight_tuple = (
                local_time.tm_year,
                local_time.tm_mon,
                local_time.tm_mday,
                0,
                0,
                0,
                0,
                0,
                local_time.tm_isdst,
            )
            midnight_ts = time_module.mktime(midnight_tuple)

            cursor = db.execute(
                """
                SELECT COALESCE(SUM(cost_usd), 0.0)
                FROM usage_events
                WHERE ts >= ?
                """,
                (midnight_ts,),
            )
            result = cursor.fetchone()
            return result[0] if result else 0.0
        except Exception as e:
            logger.exception("Failed to compute today_usd: %s", e)
            return 0.0

    def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            try:
                self._db.close()
            except Exception as e:
                logger.exception("Failed to close usage DB: %s", e)
            finally:
                self._db = None
