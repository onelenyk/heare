"""Text as a channel: a line typed is a turn, a reply is printed as written.

Equal to the voice in every way the core cares about. Enter is the end
of a turn, the way silence is for speech.
"""

from __future__ import annotations

import asyncio
import sys
from typing import AsyncIterator, TextIO

from heare.core import Message, Port


class Terminal:
    name = "text"
    # Printed text cannot be held and carried on with.
    abilities: frozenset[str] = frozenset()

    def __init__(self, source: TextIO | None = None, sink: TextIO | None = None) -> None:
        self._source = source or sys.stdin
        self._sink = sink or sys.stdout
        self._speaking = False
        self._stop = False
        # Set when the source runs dry — a pipe has ended, or Ctrl+D.
        self.closed = asyncio.Event()

    async def run(self, port: Port) -> None:
        while True:
            line = await asyncio.to_thread(self._source.readline)
            if not line:
                self.closed.set()
                return
            if text := line.strip():
                port.heard(Message(text=text, channel=self.name))

    @property
    def speaking(self) -> bool:
        return self._speaking

    async def say(self, pieces: AsyncIterator[str]) -> str:
        self._speaking, self._stop = True, False
        wrote = False
        try:
            async for piece in pieces:
                if self._stop:
                    return "stopped"
                self._sink.write(piece)
                self._sink.flush()
                wrote = True
            return "done"
        finally:
            if wrote:
                self._sink.write("\n")
                self._sink.flush()
            self._speaking = False

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def stop(self) -> None:
        self._stop = True
