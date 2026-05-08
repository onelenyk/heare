"""Heare memory store: SQLite + FTS5 backing for the heare-memory MCP server.

Public surface intentionally small: open the DB and let the MCP tools (in
``src/store/memory/server.py``, landed in US-002) drive everything else. The schema
lives in ``schema.sql`` next to this module so it can be inspected and
diff-reviewed independently of the Python wrapper.
"""
from __future__ import annotations

from .store import DEFAULT_DB_PATH, SCHEMA_PATH, open_memory_db


__all__ = ["DEFAULT_DB_PATH", "SCHEMA_PATH", "open_memory_db"]
