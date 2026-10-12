"""What is happening now, and everything that happened, in one place.

The old app answered "what is it doing?" from five places that disagreed
— a State store mirrored by pollers, a log, a telemetry file, the
database and the dashboard's own guess. Here there is one: the core
writes every change as an event, and the present is whatever the events
add up to. A screen, a journal and `doctor` are all just readers of it.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field

from heare.core.forms import Permission, Task

JOURNAL = 200


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
    parts: dict[str, list[str]] = field(
        default_factory=lambda: {"inputs": [], "outputs": [], "executors": []}
    )
    # What is going on around it, as its inputs last reported.
    around: dict[str, object] = field(default_factory=dict)
    journal: deque = field(default_factory=lambda: deque(maxlen=JOURNAL))
    _readers: list[asyncio.Queue] = field(default_factory=list)

    def emit(self, kind: str, **data) -> Event:
        event = Event(kind, data, time.time())
        self.journal.append(event)
        for reader in self._readers:
            try:
                reader.put_nowait(event)
            except asyncio.QueueFull:
                pass  # a reader that stopped reading does not stop the talk
        return event

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
            "around": self.around,
        }
