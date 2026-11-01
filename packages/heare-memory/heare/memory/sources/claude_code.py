"""Claude Code's memory: one Markdown note per fact, under each project."""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterator
from pathlib import Path

from heare.memory.sources import TEXT_CHARS, Record

logger = logging.getLogger("heare.memory.sources")

PERSONAL = {"user", "feedback"}
_HEAD = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
_TYPE = re.compile(r"^\s*(?:type|memory_type):\s*[\"']?(\w+)", re.MULTILINE)


class ClaudeCode:
    name = "claude-code"

    def __init__(self, root: Path) -> None:
        self.root = root

    def _notes(self) -> list[Path]:
        return [note for note in sorted(self.root.glob("*/memory/*.md"))
                if note.name != "MEMORY.md"]

    def found(self) -> bool:
        return bool(self._notes())

    def count(self) -> int:
        return len(self._notes())

    def _record(self, note: Path) -> Record | None:
        try:
            raw = note.read_text(encoding="utf-8")
            at = note.stat().st_mtime
        except (OSError, UnicodeDecodeError):
            logger.warning("a note could not be read: %s", note.name)
            return None
        head = _HEAD.match(raw)
        kind = _TYPE.search(head.group(1)) if head else None
        body = raw[head.end():] if head else raw
        kind_name = kind.group(1) if kind else "note"
        digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
        project = note.parent.parent.name
        return Record(f"claude-code:{project}/{note.name}#{digest}", body.strip()[:TEXT_CHARS],
                      project, kind_name, at, kind_name in PERSONAL)

    def scan(self) -> Iterator[Record]:
        for note in self._notes():
            record = self._record(note)
            if record is not None:
                yield record

    def search(self, query: str, limit: int = 5) -> list[Record]:
        words = {word.casefold() for word in query.split() if len(word) > 2}
        scored = [(sum(word in record.text.casefold() for word in words), record)
                  for record in self.scan()]
        hits = sorted((pair for pair in scored if pair[0]), key=lambda pair: -pair[0])
        return [record for _score, record in hits[:limit]]
