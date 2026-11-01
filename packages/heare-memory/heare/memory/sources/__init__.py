"""Other tools' memory, read and never written.

An adapter says what a source holds; it decides nothing. ``personal`` marks what
may be about the owner rather than about code: only that is offered to the
import, because a fact about a bug in someone's driver is not a fact about them.
A store of another program may be of any version: an adapter that cannot read
one says so through ``found`` and yields nothing, and never raises at the brain.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger("heare.memory.sources")

TEXT_CHARS = 2000  # a session summary can run for pages


@dataclass(frozen=True)
class Record:
    key: str          # stable: re-reading the source gives the same key
    text: str
    project: str
    kind: str
    at: float
    personal: bool


class Source(Protocol):
    name: str

    def found(self) -> bool: ...
    def count(self) -> int: ...
    def scan(self) -> Iterator[Record]: ...
    def search(self, query: str, limit: int = 5) -> list[Record]: ...


@contextmanager
def read_only(path: Path) -> Iterator[sqlite3.Connection]:
    """Opened so that a write cannot happen, whatever the code around it does."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.5)
    connection.row_factory = sqlite3.Row
    # Their notes hold cut emoji; one bad byte must not end the whole read.
    connection.text_factory = lambda raw: raw.decode("utf-8", "replace")
    try:
        yield connection
    finally:
        connection.close()


def columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


class SQLiteSource:
    """A notes table in someone else's database; the two differ by their rows."""

    name = ""
    table = "observations"
    needs: frozenset[str] = frozenset()
    fts = ""

    def __init__(self, path: Path) -> None:
        self.path = path

    def found(self) -> bool:
        try:
            if not self.path.is_file():
                return False
            with read_only(self.path) as connection:
                return self.needs <= columns(connection, self.table)
        except sqlite3.Error:
            logger.warning("%s cannot be read", self.name)
            return False

    def _live(self) -> str:
        return "1"

    def count(self) -> int:
        if not self.found():
            return 0
        with read_only(self.path) as connection:
            query = f"SELECT count(*) FROM {self.table} WHERE {self._live()}"
            return int(connection.execute(query).fetchone()[0])

    def _record(self, row: sqlite3.Row) -> Record:
        raise NotImplementedError

    def scan(self) -> Iterator[Record]:
        if not self.found():
            return
        try:
            with read_only(self.path) as connection:
                query = f"SELECT * FROM {self.table} WHERE {self._live()} ORDER BY id"
                for row in connection.execute(query):
                    yield self._record(row)
        except sqlite3.Error:
            logger.warning("%s stopped being readable", self.name)

    def search(self, query: str, limit: int = 5) -> list[Record]:
        words = ['"' + word.replace('"', "") + '"' for word in query.split() if len(word) > 2]
        if not words or not self.found():
            return []
        match = " OR ".join(words)
        sql = (f"SELECT o.* FROM {self.fts} f JOIN {self.table} o ON o.id = f.rowid "
               f"WHERE {self.fts} MATCH ? AND {self._live()} ORDER BY rank LIMIT ?")
        try:
            with read_only(self.path) as connection:
                return [self._record(row) for row in connection.execute(sql, (match, limit))]
        except sqlite3.Error:
            logger.warning("%s could not be searched", self.name)
            return []


def all_sources(home: Path | None = None) -> list[Source]:
    from heare.memory.sources.claude_code import ClaudeCode
    from heare.memory.sources.claude_mem import ClaudeMem
    from heare.memory.sources.engram import Engram

    home = home or Path.home()
    return [ClaudeCode(home / ".claude" / "projects"),
            Engram(home / ".engram" / "engram.db"),
            ClaudeMem(home / ".claude-mem" / "claude-mem.db")]
