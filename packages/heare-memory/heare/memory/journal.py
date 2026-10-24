"""The append-only record of what happened, one file a day.

The conversation is not the only thing worth remembering, but it is the
only thing a restart has to put back. Conversation turns are written by
``Book.keep`` — SQLite and this journal from one place — while the writer
here records the system events the core announces: work, verbs and
permissions. ``Core.hydrate`` reads the turns back from the journal, so
the next process starts with the last turns instead of an empty room.
Nothing here rewrites a line: forgetting is another event, and the
originals stay on disk for the export the owner may ask for.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime
from pathlib import Path

from heare.core.state import State

logger = logging.getLogger("heare.memory.journal")

_DIR_MODE = 0o700
_FILE_MODE = 0o600
_STATE_VERSION = 1


class Journal:
    """Events under ``root``: ``YYYY-MM-DD.jsonl``, one JSON object a line.

    ``append`` writes both conversation and system events; ``run`` mirrors
    only the system events of a live state.
    """

    def __init__(
        self, root: str | Path, *, now: Callable[[], float] = time.time
    ) -> None:
        self._root = Path(root)
        self._now = now

    def append(
        self,
        kind: str,
        payload: dict,
        *,
        source: str,
        trust: str,
        trace_id: str = "",
        at: float | None = None,
    ) -> dict:
        """Append one event. The whole line is written before the handle
        closes, so a crash leaves either all of it or none of it — a torn
        line would make the next reader skip a real event."""
        moment = self._now() if at is None else at
        stamp = datetime.fromtimestamp(moment).astimezone()
        event = {
            "id": f"ev_{uuid.uuid4().hex}",
            "at": stamp.isoformat(timespec="milliseconds"),
            "kind": kind,
            "source": source,
            "trust": trust,
            "trace_id": trace_id,
            "payload": payload,
        }
        line = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
        path = self._root / f"{stamp:%Y-%m-%d}.jsonl"
        self._root.mkdir(parents=True, exist_ok=True, mode=_DIR_MODE)
        os.chmod(self._root, _DIR_MODE)
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, _FILE_MODE)
        try:
            os.fchmod(fd, _FILE_MODE)
            _write_all(fd, line)
        finally:
            os.close(fd)
        return event

    def events(self) -> Iterator[dict]:
        """Every readable event, oldest file first. A bad line is a bad
        line: it is skipped rather than allowed to stop the history."""
        try:
            paths = sorted(p for p in self._root.glob("*.jsonl") if p.is_file())
        except OSError:
            return
        for path in paths:
            try:
                with path.open("r", encoding="utf-8", errors="replace") as lines:
                    for line in lines:
                        try:
                            row = json.loads(line)
                        except (TypeError, ValueError):
                            continue
                        if (
                            isinstance(row, dict)
                            and "id" in row
                            and "kind" in row
                        ):
                            yield row
            except OSError:
                continue

    def latest(self, limit: int = 50) -> list[dict]:
        """The newest events, newest first, without what was forgotten.

        Read from the end so a page asking every few seconds does not walk
        the whole history. A tombstone is always newer than what it buries,
        so it is met first and the buried event is skipped on the way.
        """
        if limit <= 0:
            return []
        try:
            paths = sorted(
                (p for p in self._root.glob("*.jsonl") if p.is_file()),
                reverse=True,
            )
        except OSError:
            return []
        forgotten: set[str] = set()
        found: list[dict] = []
        for path in paths:
            try:
                lines = path.read_bytes().splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                row = _parse(line)
                if row is None:
                    continue
                if row.get("kind") == "forget":
                    forgotten.update(_payload(row).get("ids", ()))
                if row.get("id") in forgotten:
                    continue
                found.append(row)
                if len(found) == limit:
                    return found
        return found

    def new_events(self, state_path: str | Path) -> tuple[list[dict], dict]:
        """The events this reader has not consumed yet, and the state that
        records how far it got. Only whole lines are consumed, so a line
        still being written is read next time rather than skipped."""
        state = _read_state(Path(state_path))
        offsets: dict[str, int] = state["files"]
        events: list[dict] = []
        files: dict[str, int] = {}
        try:
            paths = sorted(p for p in self._root.glob("*.jsonl") if p.is_file())
        except OSError:
            paths = []
        for path in paths:
            start = offsets.get(path.name, 0)
            try:
                size = path.stat().st_size
                if start > size:
                    start = 0
                with path.open("rb") as handle:
                    handle.seek(start)
                    data = handle.read()
            except OSError:
                files[path.name] = start
                continue
            consumed = data.rfind(b"\n") + 1
            for line in data[:consumed].splitlines():
                row = _parse(line)
                if row is not None:
                    events.append(row)
            files[path.name] = start + consumed
        return events, {"version": _STATE_VERSION, "files": files}

    def save_state(self, state_path: str | Path, state: dict) -> None:
        """Write the cursor atomically and privately: a crash leaves the
        old one, never a half-written file that reads as the start."""
        path = Path(state_path)
        parent = path.parent
        parent.mkdir(parents=True, exist_ok=True, mode=_DIR_MODE)
        os.chmod(parent, _DIR_MODE)
        fd, tmp = tempfile.mkstemp(
            dir=parent, prefix=".consolidate-", suffix=".json"
        )
        try:
            os.fchmod(fd, _FILE_MODE)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def tail_messages(self, limit: int = 12) -> list[dict]:
        """The last turns as the core's model expects them. Read once, so
        a line appended while building this tail is not half the answer."""
        rows = list(self.events())
        forgotten = {
            ident
            for row in rows
            if row.get("kind") == "forget"
            for ident in _payload(row).get("ids", ())
        }
        messages: list[dict] = []
        for row in rows:
            kind = row.get("kind")
            if row.get("id") in forgotten or kind not in ("heard", "said"):
                continue
            text = _payload(row).get("text", "")
            if not isinstance(text, str) or not text.strip():
                continue
            messages.append({
                "role": "user" if kind == "heard" else "assistant",
                "content": text,
            })
        return messages[-limit:] if limit > 0 else []

    def forget(self, ids: Iterable[str], *, by: str = "user") -> dict:
        """A tombstone, not a deletion: the events stay readable to an
        export, and stop feeding the conversation through this marker."""
        return self.append(
            "forget",
            {"ids": sorted(set(ids)), "by": by},
            source="system",
            trust="user_confirmed",
        )

    def export(self, path: str | Path) -> int:
        """Everything, tombstones included, in one file. Returns how many
        events were written."""
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        fd = os.open(
            destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE
        )
        try:
            os.fchmod(fd, _FILE_MODE)
            for event in self.events():
                line = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
                _write_all(fd, line)
                count += 1
        finally:
            os.close(fd)
        return count

    async def run(self, state: State) -> None:
        """Mirror the state's events until cancelled. A write that fails
        is logged and the next event is still taken: a broken disk must
        not take the conversation with it."""
        reader = state.subscribe(size=10_000)
        while True:
            event = await reader.get()
            try:
                self._record(event.kind, event.data)
            except (OSError, TypeError, ValueError):
                logger.exception("journal: %s could not be written", event.kind)

    def _record(self, kind: str, data: dict) -> None:
        if kind == "task":
            self.append("task", dict(data), source="hands", trust="tool_output")
        elif kind == "verb":
            self.append("verb", dict(data), source="core", trust="system")
        elif kind in ("permission", "permission_closed", "judged"):
            self.append(kind, dict(data), source="brain", trust="system")


def _write_all(fd: int, data: bytes) -> None:
    """Keep writing until the whole buffer is out: ``os.write`` may take
    fewer bytes than asked, and a half-written line is a lost event."""
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _payload(row: dict) -> dict:
    """A row's payload, or an empty one: old or hand-edited lines may not
    have the shape this version writes."""
    found = row.get("payload")
    return found if isinstance(found, dict) else {}


def _read_state(path: Path) -> dict:
    """The saved offsets, or none. A missing, broken or future-version
    state means "read everything", never a reason to fail a pass."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": _STATE_VERSION, "files": {}}
    if not isinstance(raw, dict) or raw.get("version") != _STATE_VERSION:
        return {"version": _STATE_VERSION, "files": {}}
    saved = raw.get("files")
    if not isinstance(saved, dict):
        return {"version": _STATE_VERSION, "files": {}}
    files = {
        name: offset
        for name, offset in saved.items()
        if isinstance(name, str)
        and isinstance(offset, int)
        and not isinstance(offset, bool)
        and offset >= 0
    }
    return {"version": _STATE_VERSION, "files": files}


def _parse(line: bytes) -> dict | None:
    """One line under the same forgiving rules as ``events``: a bad line
    is skipped, not allowed to stop the history."""
    try:
        row = json.loads(line.decode("utf-8", errors="replace"))
    except (TypeError, ValueError):
        return None
    if isinstance(row, dict) and "id" in row and "kind" in row:
        return row
    return None
