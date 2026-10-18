"""Agent CLIs as executors: whichever is installed, through one protocol.

The assistant does not do the work. It knows who can, hands the task
over, shows what they are doing, asks the person before anything is
changed, and stops them when told to. Which agent that is — opencode,
Claude Code, the next one — is a line in ``KNOWN`` and nothing else here
knows the difference: all of them speak ACP.

Reading is free; a change is asked about. The question goes to the core
(``desk.permit``), which asks the person and catches the answer in code.
An agent that does not ask cannot be made to from here, so each one is
started with a configuration that makes it ask (see ``_opencode_env``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from heare.core import Desk
from heare.reach import acp
from heare.reach.life import guarded

logger = logging.getLogger("heare.reach")

# How long an agent may say nothing before it is called stuck, and how long one
# task may run in total. A coder thinks for minutes, so idle is generous.
IDLE_S = 5 * 60.0
TOTAL_S = 30 * 60.0

# What an agent may do without asking: looking is not changing.
_READING = frozenset({"read", "search", "think", "fetch"})
# A request, as a person would say it. An agent's own title is often
# just a path or a command line.
_DOING = {
    "edit": "змінити файл", "delete": "видалити", "move": "перемістити",
    "execute": "виконати команду",
}


def _what(request: dict) -> str:
    """What the agent wants to do, in words fit to be said aloud."""
    call = request.get("toolCall") or {}
    raw = call.get("rawInput") or {}
    target = raw.get("command") or raw.get("file_path") or raw.get("filePath") \
        or raw.get("path") or call.get("title") or ""
    target = str(target)
    if "/" in target and " " not in target:
        target = Path(target).name  # the name is enough to say; the screen has the rest
    doing = _DOING.get(call.get("kind") or "", "зробити")
    return f"{doing} {target}".strip()


@dataclass(frozen=True)
class Known:
    name: str
    binary: str  # what must be on PATH for this agent to be usable
    command: tuple[str, ...]
    about: str


KNOWN: tuple[Known, ...] = (
    Known("opencode", "opencode", ("opencode", "acp"),
          "агент у терміналі: код, файли, команди, пошук в інтернеті"),
    Known("claude", "claude",
          ("npx", "-y", "@agentclientprotocol/claude-agent-acp@0.81.2"),
          "Claude Code: код, файли, команди; сильніший у складних задачах"),
)


def installed() -> list[Known]:
    """The known agents this machine actually has."""
    return [k for k in KNOWN if shutil.which(k.binary) and shutil.which(k.command[0])]


# Commands that only look. Everything else a shell can do is asked about.
_LOOKING = (
    "ls", "cat", "head", "tail", "wc", "pwd", "echo", "date", "which", "file",
    "stat", "tree", "find", "grep", "rg", "sort", "uniq", "cut", "du", "df",
    "ps", "uname", "whoami", "git status", "git log", "git diff", "git show",
    "git branch",
)


# The agent's own key (opencode's default provider) expired on 17 Oct. The
# model is chosen per session, not in the config: an ACP session has its own
# model option, and only naming it there avoids the expired default.
MODEL = "deepseek/deepseek-flash"


def _opencode_config() -> dict:
    bash = {"*": "ask"}
    for command in _LOOKING:
        bash[command] = "allow"
        bash[f"{command} *"] = "allow"
    return {
        "$schema": "https://opencode.ai/config.json",
        "permission": {"edit": "ask", "bash": bash, "webfetch": "allow"},
    }


def _opencode_env(home: Path) -> dict[str, str]:
    """opencode with a configuration of the assistant's own, in which a
    write or a command that changes something is asked about. Measured on
    25 September: with its defaults it wrote a file after being told no,
    because it never asked. The person's own opencode configuration is
    not touched; this file is the assistant's and is rewritten to match."""
    config = home / "opencode"
    config.mkdir(parents=True, exist_ok=True)
    path = config / "opencode.json"
    wanted = json.dumps(_opencode_config(), indent=2)
    try:
        current = path.read_text(encoding="utf-8")
    except OSError:
        current = ""
    if current != wanted:
        path.write_text(wanted, encoding="utf-8")
    return {**os.environ, "OPENCODE_CONFIG_DIR": str(config),
            "OPENCODE_CONFIG": str(path)}


class Agent:
    """One agent CLI in the form the core asks of an executor."""

    def __init__(self, known: Known, *, cwd: str | Path | None = None,
                 home: Path | None = None, command: list[str] | None = None,
                 env: dict[str, str] | None = None,
                 tap: Callable[[str, dict], None] | None = None,
                 mcp_servers: list[dict] | None = None) -> None:
        self.name = known.name
        self.about = known.about
        self._known = known
        self._cwd = cwd
        self._home = home or Path.home() / ".heare"
        self._command = command or list(known.command)
        self._env = env
        self._tap = tap
        self._mcp = mcp_servers or []
        self._agent: acp.Agent | None = None
        self._desks: dict[str, Desk] = {}
        self._refused: dict[str, list[str]] = {}
        self._starting = asyncio.Lock()

    async def _started(self) -> acp.Agent:
        """One process for every task, started when first needed."""
        async with self._starting:
            if self._agent is None:
                env = self._env
                if env is None and self.name == "opencode":
                    env = _opencode_env(self._home)
                logs = self._home / "logs"
                logs.mkdir(parents=True, exist_ok=True)
                agent = acp.Agent(
                    self.name, on_permission=self._decide, cwd=self._cwd,
                    stderr=logs / f"agent-{self.name}.log",
                    command=self._command, env=env, tap=self._tap,
                    mcp_servers=self._mcp,
                )
                await agent.start()
                self._agent = agent
            return self._agent

    async def _decide(self, session: acp.Session, request: dict) -> str:
        desk = self._desks.get(session.id)
        if desk is None:
            return "reject"
        call = request.get("toolCall") or {}
        kind = "read" if call.get("kind") in _READING else "change"
        what = _what(request)
        allowed = await desk.permit(what, kind)
        if not allowed:
            self._refused.setdefault(session.id, []).append(what)
        return "allow" if allowed else "reject"

    async def run(self, task: str, desk: Desk) -> str:
        agent = await self._started()

        def on_event(event: acp.Event) -> None:
            self._activity = time.monotonic()
            if event.kind == "tool" and event.text:
                desk.step(event.text[:80])

        session = await agent.session(on_event)
        # Name the model on the session itself: its default is the provider
        # whose key has expired, and the session's own list has a working one.
        await session.set("model", MODEL)
        self._desks[session.id] = desk
        self._activity = time.monotonic()
        try:
            await guarded(session.prompt(task), lambda: self._activity,
                          idle_s=IDLE_S, total_s=TOTAL_S, stop=session.cancel)
        except asyncio.CancelledError:
            # Being cancelled is how it is told to stop — and it must.
            try:
                await asyncio.shield(session.cancel())
            except Exception:  # noqa: BLE001
                logger.exception("%s: could not be told to stop", self.name)
            raise
        finally:
            self._desks.pop(session.id, None)
            refused = self._refused.pop(session.id, [])
        if session.status == "cancelled":
            return "Зупинено."
        # Some models leak their thinking into the answer; what follows
        # the last closing tag is the answer.
        said = session.text.rpartition("</think>")[2].strip()
        if refused:
            # Stated by the code, not left to the agent: an agent told no
            # often says nothing, and silence was being retold as success.
            return (f"НЕ зроблено, бо людина сама щойно сказала «ні» на запит: "
                    f"{'; '.join(refused)}. {said}").strip()
        return said or "Агент закінчив і нічого не повідомив; чи зроблено — невідомо."

    async def close(self) -> None:
        if self._agent is not None:
            await self._agent.close()
            self._agent = None


def connect(settings: Any) -> list[Agent]:
    """The agent the settings name (``executor``; «all» for every
    installed one), working in its own folder (``executor_cwd``)."""
    only = str(getattr(settings, "executor", "") or "").strip().lower()
    cwd = getattr(settings, "executor_cwd", "") or None
    if cwd:
        Path(cwd).mkdir(parents=True, exist_ok=True)
    found = [k for k in installed() if only in ("", "all") or k.name == only]
    return [Agent(k, cwd=cwd) for k in found]
