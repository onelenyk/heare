"""What the agent does, as a terminal shows it: what was asked, what it ran,
what came back, what it said. Built from the ACP wire, not from the agent's
own log, so it is the same for every agent CLI."""

from __future__ import annotations

import collections

# Enough to scroll back through one piece of work; the page gets all of it
# on every report, so not more.
LINES = 300
# Of a command's output only the start is worth reading on a page.
OUTPUT_LINES = 6
WIDTH = 160


def _cut(text: str, width: int = WIDTH) -> str:
    return text[:width] + "…" if len(text) > width else text


def _output(update: dict) -> str:
    """The text a finished tool call returned, wherever the agent put it."""
    parts = []
    for block in update.get("content") or []:
        inner = block.get("content") if isinstance(block, dict) else None
        if isinstance(inner, dict) and inner.get("type") == "text":
            parts.append(str(inner.get("text") or ""))
    if not parts:
        raw = update.get("rawOutput")
        if isinstance(raw, dict):
            raw = raw.get("output") or raw.get("stdout") or ""
        if isinstance(raw, str):
            parts.append(raw)
    return "\n".join(parts).strip()


class Terminal:
    def __init__(self) -> None:
        self.lines: collections.deque[str] = collections.deque(maxlen=LINES)
        self._said = ""
        self._shown: set[str] = set()
        self._titles: dict[str, str] = {}
        # Counts every line written, so a full screen still reads as changed.
        self.written = 0

    def _add(self, line: str) -> None:
        self.lines.append(line)
        self.written += 1

    def text(self) -> str:
        return "\n".join(self.lines)

    def begin(self, request: str, *, new: bool) -> None:
        """A piece of work is taken. New work is set apart from the last."""
        if new and self.lines:
            self._add("")
            self._add("──── нова справа ────")
        self._add(f"› {_cut(' '.join(request.split()))}")

    def end(self, status: str, result: str) -> None:
        self._flush()
        if status == "stopped":
            self._add("■ зупинено")
        elif status == "failed":
            self._add(f"✗ {_cut(result)}")

    def feed(self, direction: str, message: dict) -> None:
        """One message of the ACP wire; only what the agent did is kept."""
        if direction != "in" or message.get("method") != "session/update":
            return
        update = (message.get("params") or {}).get("update") or {}
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            self._said += str((update.get("content") or {}).get("text") or "")
        elif kind in ("tool_call", "tool_call_update"):
            self._call(update)
            if update.get("status") == "completed":
                self._done(update)
            elif update.get("status") == "failed":
                self._add(f"  ✗ {_cut(_output(update) or 'не вийшло')}")

    def _call(self, update: dict) -> None:
        """A call is shown once: as soon as it says what it runs, or, if it
        never does, by its title when it ends."""
        call_id = str(update.get("toolCallId") or "")
        if call_id and call_id in self._shown:
            return
        if update.get("title"):
            self._titles[call_id] = str(update["title"])
        raw = update.get("rawInput")
        raw = raw if isinstance(raw, dict) else {}
        command = raw.get("command")
        path = raw.get("file_path") or raw.get("path")
        title = self._titles.get(call_id) or str(update.get("kind") or "")
        if command:
            line = f"$ {_cut(str(command))}"
        elif path:
            line = f"• {_cut(f'{title} {path}'.strip())}"
        elif update.get("status") in ("completed", "failed") and title:
            line = f"• {_cut(title)}"
        else:
            return
        self._flush()
        self._add(line)
        self._shown.add(call_id)

    def _done(self, update: dict) -> None:
        out = _output(update)
        if not out:
            return
        lines = out.splitlines()
        for line in lines[:OUTPUT_LINES]:
            self._add(f"  {_cut(line)}")
        if len(lines) > OUTPUT_LINES:
            self._add(f"  … ще {len(lines) - OUTPUT_LINES} рядків")

    def _flush(self) -> None:
        said = " ".join(self._said.rpartition("</think>")[2].split())
        self._said = ""
        if said:
            self._add(f"агент: {said}")
