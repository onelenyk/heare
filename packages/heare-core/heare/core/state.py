"""What is happening now, and everything that happened, in one place.

The old app answered "what is it doing?" from five places that disagreed
— a State store mirrored by pollers, a log, a telemetry file, the
database and the dashboard's own guess. Here there is one: the core
writes every change as an event, and the present is whatever the events
add up to. A screen, a journal and `doctor` are all just readers of it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from heare.core.forms import Permission, Question, Task

logger = logging.getLogger("heare.core.state")
JOURNAL = 200
_UNSET = object()


@dataclass(frozen=True)
class Event:
    kind: str
    data: dict
    at: float

    def view(self) -> dict:
        return {"kind": self.kind, "at": self.at, **self.data}


@dataclass
class State:
    # idle · hearing · thinking · speaking
    phase: str = "idle"
    tasks: dict[int, Task] = field(default_factory=dict)
    permission: Permission | None = None
    question: Question | None = None
    parts: dict[str, list[str]] = field(
        default_factory=lambda: {"inputs": [], "outputs": [], "executors": []}
    )
    # Keyed by (channel, key): two parts cannot erase each other's fact.
    around: dict[tuple[str, str], object] = field(default_factory=dict)
    # The newest value of each key, whichever part said it last.
    _latest: dict[str, tuple[str, object]] = field(default_factory=dict)
    journal: deque = field(default_factory=lambda: deque(maxlen=JOURNAL))
    dropped: int = 0
    _readers: list[asyncio.Queue] = field(default_factory=list)

    def emit(self, kind: str, **data) -> Event:
        event = Event(kind, data, time.time())
        self.journal.append(event)
        for reader in self._readers:
            try:
                reader.put_nowait(event)
            except asyncio.QueueFull:
                self.dropped += 1
                logger.warning("an event reader is full; %s dropped", kind)
        return event

    def note(self, channel: str, key: str, value: object) -> bool:
        """Keep a fact under the part that reported it; true when it changed."""
        if self.around.get((channel, key), _UNSET) == value:
            return False
        self.around[(channel, key)] = value
        self._latest[key] = (channel, value)
        return True

    def around_any(self, key: str) -> object | None:
        """The newest value of a key, from whichever part said it last."""
        latest = self._latest.get(key)
        return latest[1] if latest is not None else None

    def set_phase(self, phase: str) -> None:
        if phase != self.phase:
            self.phase = phase
            self.emit("phase", phase=phase)

    def subscribe(self, size: int = 1000) -> asyncio.Queue:
        """Every event from now on. The reader owns the queue."""
        reader: asyncio.Queue = asyncio.Queue(maxsize=size)
        self._readers.append(reader)
        return reader

    def unsubscribe(self, reader: asyncio.Queue) -> None:
        if reader in self._readers:
            self._readers.remove(reader)

    def snapshot(self) -> dict:
        return {
            "phase": self.phase,
            "parts": self.parts,
            "tasks": [t.view() for t in self.tasks.values()],
            "permission": self.permission.view() if self.permission else None,
            "question": self.question.view() if self.question else None,
            # Flat as `/api/state` and the page always saw it.
            "around": {key: value for key, (_channel, value) in self._latest.items()},
        }
