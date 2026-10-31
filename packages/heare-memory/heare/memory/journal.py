"""The append-only record of what happened, one file a day.

The conversation is not the only thing worth remembering, but it is the
only thing a restart has to put back. Conversation turns are written by
``Book.keep`` — SQLite and this journal from one place — while the writer
here records the system events the core announces: work, verbs and
permissions. ``Core.hydrate`` reads the turns back from the journal, so
the next process starts with the last turns instead of an empty room.
Appending never rewrites a line: forgetting is another event. A forget
does one deliberate exception — ``compact`` drops the named events, and
their words, from the day files, so what the owner asked to forget is not
left on disk in readable form; everything else stays append-only.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from heare.core.state import State

logger = logging.getLogger("heare.memory.journal")

_DIR_MODE = 0o700
_FILE_MODE = 0o600
_STATE_VERSION = 1

# One lock for every day file. Taking it before a file is opened is what
# makes compaction safe: an appender blocked on this lock opens the day
# file only after ``os.replace`` has put the new inode in place, so its
# line cannot land in the unlinked old one.
_LOCK_NAME = ".lock"


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
        created_dir = not self._root.exists()
        self._root.mkdir(parents=True, exist_ok=True, mode=_DIR_MODE)
        os.chmod(self._root, _DIR_MODE)
        # Two processes append to the same day file. O_APPEND chooses the
        # offset atomically but does not stop one writer's second write()
        # landing inside the other's line; the lock does. It is taken
        # before the file is opened so a compaction that replaces the
        # file while this waits cannot leave the line in an unlinked
        # inode.
        with self._locked():
            created_file = not path.exists()
            fd = os.open(
                path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, _FILE_MODE
            )
            try:
                os.fchmod(fd, _FILE_MODE)
                _write_all(fd, line)
                # The line is only returned after it is on the platter, so
                # a caller that pairs this with another store never
                # believes a write that a power loss still has in the page
                # cache.
                os.fsync(fd)
            finally:
                os.close(fd)
        if created_file:
            _fsync_dir(self._root)
        if created_dir:
            _fsync_dir(self._root.parent)
        return event

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """The one lock append and compact share across every day file."""
        fd = os.open(
            self._root / _LOCK_NAME, os.O_CREAT | os.O_RDWR, _FILE_MODE
        )
        try:
            os.fchmod(fd, _FILE_MODE)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def compact(self, ids: Iterable[str]) -> int:
        """Drop these events, and their words, from every day file.

        The one deliberate rewrite of an append-only journal: only the
        events a forget names are removed, so the owner's request is kept
        on disk as well as in the tombstone. Each affected file is
        replaced atomically, under the same lock an append takes, so a
        crash leaves the old file whole or the new one whole, never a
        half-written day. Returns how many events were dropped.
        """
        wanted = {ident for ident in ids if isinstance(ident, str)}
        if not wanted:
            return 0
        removed = 0
        with self._locked():
            try:
                paths = sorted(
                    p for p in self._root.glob("*.jsonl") if p.is_file()
                )
            except OSError:
                return 0
            for path in paths:
                try:
                    data = path.read_bytes()
                except OSError:
                    continue
                kept = bytearray()
                dropped = 0
                for line in data.splitlines(keepends=True):
                    row = _parse(line)
                    if row is not None and row.get("id") in wanted:
                        dropped += 1
                        continue
                    kept += line
                if not dropped:
                    continue
                self._rewrite(path, bytes(kept))
                removed += dropped
        return removed

    def _rewrite(self, path: Path, data: bytes) -> None:
        """Replace ``path`` with ``data`` through a fsynced temp file.

        ``os.replace`` is the only moment the old file stops existing, so
        a crash before it leaves every line — including a forgotten one —
        exactly where it was; the next forget can try again.
        """
        fd, tmp = tempfile.mkstemp(
            dir=path.parent, prefix=".compact-", suffix=".tmp"
        )
        try:
            try:
                os.fchmod(fd, _FILE_MODE)
                _write_all(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        _fsync_dir(path.parent)

    def end(self) -> tuple[str | None, int]:
        """The position just after the last whole line, for a new reader.

        A reconciliation marker must start on a line boundary: seeking a
        byte into the middle of a line would make the next reader skip
        the rest of it. The last file is read because only its tail can
        be torn; earlier files were finished by whoever wrote them.
        """
        try:
            paths = sorted(p for p in self._root.glob("*.jsonl") if p.is_file())
        except OSError:
            return None, 0
        if not paths:
            return None, 0
        path = paths[-1]
        try:
            data = path.read_bytes()
        except OSError:
            return path.name, 0
        return path.name, data.rfind(b"\n") + 1

    def events_since(
        self, file: str | None, offset: int = 0
    ) -> Iterator[dict]:
        """Events from a saved position onward, oldest file first.

        A missing or shrunken file restarts from zero rather than being
        treated as an error: a journal that lost a day is still a journal.
        """
        try:
            paths = sorted(p for p in self._root.glob("*.jsonl") if p.is_file())
        except OSError:
            return
        for path in paths:
            if file is not None and path.name < file:
                continue
            start = offset if path.name == file else 0
            try:
                size = path.stat().st_size
                if start > size:
                    start = 0
                with path.open("rb") as handle:
                    handle.seek(start)
                    data = handle.read()
            except OSError:
                continue
            consumed = data.rfind(b"\n") + 1
            for line in data[:consumed].splitlines():
                row = _parse(line)
                if row is not None:
                    yield row

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
                    if start > 0:
                        # A compaction moves every byte after a dropped
                        # event. An old offset that is no longer on a line
                        # boundary would read as damage and stop the
                        # cursor for good, so the file is read again from
                        # the start; the caller's own record makes the
                        # repeat a no-op.
                        handle.seek(start - 1)
                        if handle.read(1) != b"\n":
                            start = 0
                    handle.seek(start)
                    data = handle.read()
            except OSError:
                files[path.name] = start
                continue
            consumed = data.rfind(b"\n") + 1
            if consumed < len(data):
                logger.warning(
                    "journal: a partial line waits at the end of %s; "
                    "the cursor stops before it",
                    path.name,
                )
            read = 0
            for line in data[:consumed].splitlines(keepends=True):
                row = _parse(line)
                if row is None:
                    # A complete but unreadable line is damage, not a
                    # line still being written. Consuming it would drop a
                    # real event without a trace, so the cursor stops
                    # here and the warning names the place.
                    logger.warning(
                        "journal: unreadable line in %s at byte %d; "
                        "the cursor stops before it",
                        path.name,
                        start + read,
                    )
                    break
                events.append(row)
                read += len(line)
            files[path.name] = start + read
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

    def forgotten_ids(self, events: Iterable[dict] | None = None) -> set[str]:
        """Ids under a ``forget`` tombstone, read from ``events`` or all.

        One shared view of what was buried, so every reader agrees on it.
        """
        rows = self.events() if events is None else events
        return {
            ident
            for row in rows
            if row.get("kind") == "forget"
            for ident in _payload(row).get("ids", ())
            if isinstance(ident, str)
        }

    def visible_events(
        self, events: Iterable[dict] | None = None
    ) -> Iterator[dict]:
        """The events a pass may read: no tombstones and nothing they bury.

        A paid pass that reads the raw journal would learn a forgotten
        line again. Every pass goes through this one filter so the
        tombstone means the same everywhere.
        """
        rows = list(self.events() if events is None else events)
        forgotten = self.forgotten_ids(rows)
        return (
            row
            for row in rows
            if row.get("kind") != "forget" and row.get("id") not in forgotten
        )

    def visible_since(
        self, file: str | None, offset: int = 0
    ) -> Iterator[dict]:
        """Visible events from a saved position, tombstones respected.

        A reconciliation must not replay a line the owner has already
        forgotten, so it uses the same view as every other pass.
        """
        rows = list(self.events_since(file, offset))
        forgotten = self.forgotten_ids(rows)
        return (
            row
            for row in rows
            if row.get("kind") != "forget" and row.get("id") not in forgotten
        )

    def tail_messages(self, limit: int = 12) -> list[dict]:
        """The last turns as the core's model expects them. Read once, so
        a line appended while building this tail is not half the answer."""
        messages: list[dict] = []
        for row in self.visible_events():
            kind = row.get("kind")
            if kind not in ("heard", "said"):
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
        """A tombstone, not a deletion: the originals stay on disk, but
        passes and an export skip them; this marker is how."""
        return self.append(
            "forget",
            {"ids": sorted(set(ids)), "by": by},
            source="system",
            trust="user_confirmed",
        )

    def export(self, path: str | Path) -> int:
        """Everything still visible, tombstones included, in one file.

        A tombstone itself is kept — it is what explains the gap — but the
        lines it buries are left out, so an export cannot hand forgotten
        words back. Returns how many events were written.
        """
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        rows = list(self.events())
        forgotten = self.forgotten_ids(rows)
        count = 0
        fd = os.open(
            destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE
        )
        try:
            os.fchmod(fd, _FILE_MODE)
            for event in rows:
                if event.get("id") in forgotten:
                    continue
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
        elif kind == "around" and data.get("key") == "playing":
            # What played nearby, when it changed: the ear's recordings are
            # weighed against it (was that the television?).
            self.append("around", dict(data), source="eyes", trust="tool_output")
        elif kind == "halted":
            self.append("halted", {"tasks": data.get("tasks", 0)},
                        source="core", trust="system")
        elif kind == "voice_stopped":
            self.append("voice_stopped", {"channel": data.get("channel", "")},
                        source="core", trust="system")
        elif kind == "failed":
            self.append("failed", {"channel": data.get("channel", "")},
                        source="core", trust="system")
        elif kind == "cannot":
            # The invented verb's name is the signal. The request text is not:
            # it is already in the heard line, and repeating it on disk twice
            # costs privacy for nothing.
            payload = {"why": data.get("why", "")}
            if data.get("why") == "no such verb":
                payload["what"] = data.get("what", "")
            self.append("cannot", payload, source="core", trust="system")
        elif kind == "question_closed" and not data.get("answer"):
            # An answered question is a normal turn; an empty answer means the
            # person never answered and the work waited 240 s for nothing.
            self.append("question_closed", {"question": data.get("id", "")},
                        source="brain", trust="system")
        elif kind == "feature" and data.get("state") == "error":
            self.append("feature", {"feature": data.get("id", ""),
                                    "state": "error"},
                        source="brain", trust="system")
        elif kind == "mind" and data.get("action") in ("rejected", "cancelled"):
            # A correction, not a system event: the person said no to a fact
            # or dropped a reminder. The id finds it; the words are already
            # in the conversation, so they are not copied here.
            self.append("mind", {"what": data.get("what", ""),
                                 "action": data.get("action", ""),
                                 "id": data.get("id")},
                        source="brain", trust="system")


def _write_all(fd: int, data: bytes) -> None:
    """Keep writing until the whole buffer is out: ``os.write`` may take
    fewer bytes than asked, and a half-written line is a lost event."""
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _fsync_dir(path: Path) -> None:
    """Make a new directory entry survive a power loss, not only its bytes."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


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
