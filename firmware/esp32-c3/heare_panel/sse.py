"""Server-sent events, parsed from whatever a socket read returned.

The brain writes ``data: <json>`` lines ended by a blank line and a
``: still here`` comment every five seconds. This module turns those bytes
into dicts and nothing else: no hardware, no HTTP headers, no allowlist.
What an event means belongs to ``state.py``; whether it may be shown
belongs to ``link.py``.

Lines are decoded only once they are complete, so a multi-byte UTF-8
character split across two reads is safe. A line longer than ``MAX_LINE``
is discarded up to its newline and counted in ``dropped``, which keeps the
buffer bounded on a board whose heap is measured in kilobytes. A payload
that is not a JSON object is dropped the same way: malformed input must
never take the stream down.
"""

import json

MAX_LINE = 4096


class SSEParser:
    MAX_LINE = MAX_LINE

    def __init__(self) -> None:
        self._line = bytearray()
        self._data: list[str] = []
        self._discarding = False
        self.dropped = 0

    @property
    def buffer_len(self) -> int:
        return len(self._line)

    def feed(self, chunk: bytes) -> list[dict]:
        events: list[dict] = []
        start = 0
        size = len(chunk)
        while start < size:
            newline = chunk.find(b"\n", start)
            if newline < 0:
                self._append(chunk[start:])
                break
            self._append(chunk[start:newline])
            self._end_line(events)
            start = newline + 1
        return events

    def _append(self, piece: bytes) -> None:
        if not piece or self._discarding:
            return
        if len(self._line) + len(piece) <= self.MAX_LINE:
            self._line.extend(piece)
            return
        # The line cannot be used, so stop keeping it and skip to its end.
        self._line.clear()
        self._discarding = True
        self.dropped += 1

    def _end_line(self, events: list[dict]) -> None:
        if self._discarding:
            self._discarding = False
            self._line.clear()
            self._data = []
            return
        raw = bytes(self._line)
        self._line.clear()
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        try:
            line = raw.decode("utf-8")
        except UnicodeDecodeError:
            self.dropped += 1
            self._data = []
            return
        if not line:
            self._dispatch(events)
            return
        if line.startswith(":"):
            return
        field, _, value = line.partition(":")
        if field != "data":
            return
        if value[:1] == " ":
            value = value[1:]
        self._data.append(value)

    def _dispatch(self, events: list[dict]) -> None:
        if not self._data:
            return
        payload = "\n".join(self._data)
        self._data = []
        try:
            event = json.loads(payload)
        except ValueError:
            self.dropped += 1
            return
        if isinstance(event, dict):
            events.append(event)
        else:
            self.dropped += 1
