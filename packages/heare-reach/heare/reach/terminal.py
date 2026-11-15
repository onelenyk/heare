"""What the agent does, as a terminal shows it: what was asked, what it ran,
what came back, what it said. Built from the server's own events, not from
the agent's log, so it is the same whatever model answers."""

from __future__ import annotations

import collections
import re
import time
from heare.core.i18n import _

# Enough to scroll back through one piece of work; the page gets all of it
# on every report, so not more.
LINES = 300
# Of a command's output only the start is worth reading on a page.
OUTPUT_LINES = 6
WIDTH = 160


def _cut(text: str, width: int = WIDTH) -> str:
    return text[:width] + "…" if len(text) > width else text


# What agents put before every command to keep pagers and prompts away:
# it says nothing about the command.
_QUIET = re.compile(r"^(?:export\s+(?:\w+=\S*\s*)+;\s*)+")

# How a tool's path is spelled across releases.
_PATH_KEYS = ("filePath", "filepath", "path")


def _command(text: str) -> str:
    """A command on one line: its first line, without the agent's own noise."""
    first, _sep, more = text.strip().partition("\n")
    first = _QUIET.sub("", first.strip())
    return _cut(first + (" …" if more.strip() else ""), 120)


def _state(part: dict) -> dict:
    state = part.get("state")
    return state if isinstance(state, dict) else {}


def _input(part: dict) -> dict:
    data = _state(part).get("input")
    return data if isinstance(data, dict) else {}


def _output(part: dict) -> str:
    """The text a finished tool call returned, wherever it was put."""
    state = _state(part)
    for value in (state.get("output"),
                  (state.get("metadata") or {}).get("output")
                  if isinstance(state.get("metadata"), dict) else None):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


class Terminal:
    def __init__(self) -> None:
        self.lines: collections.deque[str] = collections.deque(maxlen=LINES)
        self._said = ""
        self._roles: dict[str, str] = {}
        self._shown: set[str] = set()
        self._titles: dict[str, str] = {}
        # Counts every line written, so a full screen still reads as changed.
        self.written = 0
        self._running = False
        self._waiting = ""

    def _add(self, line: str) -> None:
        self.lines.append(line)
        self.written += 1

    def text(self) -> str:
        """What is on screen, and while work runs, what the agent is saying
        so far and that it is still at it."""
        tail = []
        if self._running:
            said = self._spoken()
            if said:
                tail.append(_("agent: {said}").format(said=_cut(said, 400)))
            tail.append(f"  ? {self._waiting}" if self._waiting else _("⋯ working"))
        return "\n".join([*self.lines, *tail])

    def begin(self, request: str, *, new: bool, asked: str = "",
              at: float | None = None) -> None:
        """A piece of work is taken: when, and in the person's own words if
        they are known. New work is set apart from the last."""
        if new and self.lines:
            self._add("")
            self._add(_("──── new work ────"))
        stamp = time.strftime("%H:%M", time.localtime(at))
        self._add(f"{stamp} › {_cut(' '.join((asked or request).split()))}")
        self._running = True

    def permit(self, what: str, allowed: bool | None, by: str = "person",
               why: str = "") -> None:
        """An action weighed before the agent takes it: by the gate, which
        lets it through or brings it to the person, or by the person.
        ``allowed`` is None while the answer is awaited."""
        if allowed is None:
            self._waiting = (_("the judge weighs: ") if by == "judge" else _("waiting for leave: ")) \
                + _command(what)
            return
        self._waiting = ""
        self._flush()
        if by == "judge":
            self._add(_("  ✓ by itself: {why}").format(why=_cut(why or _command(what))))
            return
        reason = _(" (asking, because {why})").format(why=_cut(why, 100)) if why else ""
        self._add(_("  ? allow: {what}{reason} → {answer}").format(
            what=_command(what), reason=reason, answer=_("yes") if allowed else _("no")))

    def end(self, status: str, result: str) -> None:
        self._running = False
        self._flush()
        if status == "stopped":
            self._add(_("■ stopped"))
        elif status == "failed":
            self._add(f"✗ {_cut(result)}")

    def feed(self, event: dict) -> None:
        """One event of the server's stream; only what the agent did is kept."""
        kind = str(event.get("type") or "")
        props = event.get("properties")
        props = props if isinstance(props, dict) else {}
        if kind == "message.updated":
            info = props.get("info") or {}
            if isinstance(info, dict):
                self._roles[str(info.get("id") or "")] = str(info.get("role") or "")
        elif kind == "message.part.delta":
            if self._roles.get(str(props.get("messageID") or "")) != "assistant":
                return
            if props.get("field") == "text":
                self._said += str(props.get("delta") or "")
        elif kind == "message.part.updated":
            part = props.get("part") or {}
            if isinstance(part, dict) and part.get("type") == "tool":
                self._tool(part)
        elif kind == "question.asked":
            questions = props.get("questions")
            questions = questions if isinstance(questions, list) else []
            for question in questions:
                text = str((question or {}).get("question") or "")
                if text:
                    self._flush()
                    self._add(f"? {_cut(text)}")

    def _tool(self, part: dict) -> None:
        call = str(part.get("callID") or part.get("id") or "")
        state = _state(part)
        status = str(state.get("status") or "")
        data = _input(part)
        title = str(part.get("tool") or "")
        if title:
            self._titles[call] = title
        if status in ("pending", "running"):
            self._call(call, part, data)
        elif status == "completed":
            self._call(call, part, data)
            self._done(part)
        elif status == "error":
            shown = _output(part)
            # A refusal is already on screen as the person's «ні»: it is not
            # a command that failed, and it never ran.
            if "rejected permission" in shown:
                return
            self._call(call, part, data)
            self._add(f"  ✗ {_cut(shown or _('failed'))}")

    def _call(self, call: str, part: dict, data: dict) -> None:
        """A call is shown once: as soon as it says what it runs, or, if it
        never does, by its title when it ends."""
        if call and call in self._shown:
            return
        command = data.get("command")
        path = next((data[key] for key in _PATH_KEYS
                     if isinstance(data.get(key), str) and data[key]), "")
        title = self._titles.get(call) or str(part.get("tool") or "")
        if command:
            line = f"$ {_command(str(command))}"
        elif path:
            line = f"• {_cut(f'{title} {path}'.strip())}"
        elif _state(part).get("status") == "completed" and title:
            line = f"• {_cut(title)}"
        else:
            return
        self._flush()
        self._add(line)
        self._shown.add(call)

    def _done(self, part: dict) -> None:
        out = _output(part)
        if not out:
            return
        lines = out.splitlines()
        for line in lines[:OUTPUT_LINES]:
            self._add(f"  {_cut(line)}")
        if len(lines) > OUTPUT_LINES:
            self._add(_("  … {n} more lines").format(n=len(lines) - OUTPUT_LINES))

    def _spoken(self) -> str:
        return " ".join(self._said.rpartition("</think>")[2].split())

    def _flush(self) -> None:
        said = self._spoken()
        self._said = ""
        if said:
            self._add(_("agent: {said}").format(said=said))
