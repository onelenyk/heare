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
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from heare.reach import runner
from heare.core import Desk
from heare.core.i18n import N_, _
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
    "edit": N_("change the file"), "delete": N_("delete"), "move": N_("move"),
    "execute": N_("run the command"),
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
    doing = _(_DOING.get(call.get("kind") or "", N_("do")))
    return f"{doing} {target}".strip()


@dataclass(frozen=True)
class Known:
    name: str
    binary: str  # what must be on PATH for this agent to be usable
    command: tuple[str, ...]
    about: str


_ABOUT = {
    "opencode": "an agent in the terminal: code, files, commands, web search",
    "claude": "Claude Code: code, files, commands; stronger at hard tasks",
}

# The table lives beside the agents so the hands that spawn one and the brain
# that configures one cannot disagree about its binary or command.
KNOWN: tuple[Known, ...] = tuple(
    Known(
        name,
        entry["binary"],
        tuple(entry["command"]),
        _ABOUT.get(name, entry["label"]),
    )
    for name, entry in runner.KNOWN.items()
)


def installed() -> list[Known]:
    """The known agents this machine actually has."""
    by_name = {known.name: known for known in KNOWN}
    return [
        by_name[found["type"]]
        for found in runner.detect()
        if found["type"] in by_name
    ]


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


# How much the agent does on its own (the owner's choice, 24 October):
# «careful» brings every change to the person; «normal» lets the judge in
# the brain weigh each one against what the person asked; «auto» asks about
# nothing. In every mode the machine's admin rights, settings and secrets
# stay out of reach — a floor no judge is asked about.
MODES = ("careful", "normal", "auto")
MODE = "normal"

# Never, in any mode.
_NEVER = (
    "sudo", "su", "security", "defaults write", "defaults delete", "systemsetup",
    "networksetup", "pmset", "spctl", "csrutil", "nvram", "diskutil", "launchctl",
    "dscl", "passwd", "chsh", "fdesetup", "tccutil",
)
# opencode's own tools that only look: never worth a question, nor a judge.
# Our own ask is a question to the person already; weighing it first asked
# twice (24 October).
_LOOKING_TOOLS = ("read", "glob", "grep", "list", "lsp", "todowrite", "todoread",
                  "webfetch", "websearch", "codesearch", "external_directory",
                  acp.ask_tool("opencode"))

# Told to the agent with every task: a rule it cannot see is a rule it
# walks into.
_RULES = {
    "careful": "",
    "normal": ("[heare rules] Delete files to the Trash: `trash <path>`, not `rm`. "
               "What cannot be undone or will be seen by others is weighed "
               "first and may be put to the owner.\n\n"),
    "auto": "[heare rules] When deleting files, use `trash <path>`, not `rm`.\n\n",
}


def _commands(rule: str, names: tuple[str, ...]) -> dict[str, str]:
    return {pattern: rule for name in names for pattern in (name, f"{name} *")}


def _opencode_config(patch: dict | None = None, mode: str = MODE) -> dict:
    """Everything that is not plainly looking comes to `_decide`, MCP tools
    too (the catch-all `*`); there the mode decides who weighs it. The last
    pattern that matches wins, so each catch-all goes first."""
    if mode == "auto":
        permission: dict = {"*": "allow", "bash": {"*": "allow"}}
    else:
        permission = {"*": "ask", **{tool: "allow" for tool in _LOOKING_TOOLS},
                      "edit": "ask", "bash": {"*": "ask", **_commands("allow", _LOOKING)}}
    permission["bash"].update(_commands("deny", _NEVER))
    config = {"$schema": "https://opencode.ai/config.json", "permission": permission}
    config.update(patch or {})
    return config


def _opencode_env(home: Path, patch: dict | None = None,
                  mode: str = MODE) -> dict[str, str]:
    """opencode with a configuration of the assistant's own, in which a
    write or a command that changes something is asked about. Measured on
    25 September: with its defaults it wrote a file after being told no,
    because it never asked. The person's own opencode configuration is
    not touched; this file is the assistant's and is rewritten to match."""
    config = home / "opencode"
    config.mkdir(parents=True, exist_ok=True)
    path = config / "opencode.json"
    wanted = json.dumps(_opencode_config(patch, mode), indent=2)
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
                 model: str | None = None,
                 config_patch: dict | None = None,
                 tap: Callable[[str, dict], None] | None = None,
                 mcp_servers: list[dict] | None = None,
                 mode: str = MODE) -> None:
        self.name = known.name
        self.mode = mode if mode in MODES else MODE
        self.about = known.about
        self._known = known
        self._cwd = cwd
        self._home = home or Path.home() / ".heare"
        self._command = command or list(known.command)
        self._env = env
        self._model = model
        self._config_patch = config_patch or {}
        self._tap = tap
        self._mcp = mcp_servers or []
        self._agent: acp.Agent | None = None
        self._last: str = ""
        self._desks: dict[str, Desk] = {}
        self._refused: dict[str, list[str]] = {}
        # Questions waiting for the person: the agent is not idle meanwhile,
        # however long they take.
        self._waiting = 0
        self._starting = asyncio.Lock()

    async def _started(self) -> acp.Agent:
        """One process for every task, started when first needed."""
        async with self._starting:
            if self._agent is None:
                env = self._env
                if self.name == "opencode":
                    permission = _opencode_env(self._home / "machine", self._config_patch,
                                               self.mode)
                    env = permission if env is None else {**permission, **env}
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
        if self.mode == "auto":
            return "allow"
        call = request.get("toolCall") or {}
        if call.get("kind") in _READING:
            kind = "read"
        else:
            kind = "judge" if self.mode == "normal" else "change"
        what = _what(request)
        # The judge is shown the action whole: which tool, and with what.
        detail = json.dumps({k: call.get(k) for k in ("kind", "title", "rawInput")
                             if call.get(k)}, ensure_ascii=False)
        self._waiting += 1
        try:
            allowed = await desk.permit(what, kind, detail)
        finally:
            self._waiting -= 1
            self._activity = time.monotonic()
        if not allowed:
            self._refused.setdefault(session.id, []).append(what)
        return "allow" if allowed else "reject"

    @property
    def last_session(self) -> str:
        return self._last

    async def run(self, task: str, desk: Desk, *, resume: str = "") -> str:
        agent = await self._started()

        def on_event(event: acp.Event) -> None:
            self._activity = time.monotonic()
            if event.kind == "tool" and event.text:
                desk.step(event.text[:80])

        session = None
        if resume:
            try:
                session = await agent.reopened(resume, on_event)
            except acp.AcpError:
                logger.info("%s: session %s is gone; a new one", self.name, resume)
        if session is None:
            session = await agent.session(on_event)
        self._last = session.id
        # Name the model on the session itself: its default is the provider
        # whose key has expired, and the session's own list has a working one.
        await session.set("model", self._model or MODEL)
        self._desks[session.id] = desk
        self._activity = time.monotonic()
        try:
            await guarded(session.prompt(_RULES[self.mode] + task),
                          lambda: time.monotonic() if self._waiting else self._activity,
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
            return "Stopped."
        # Some models leak their thinking into the answer; what follows
        # the last closing tag is the answer.
        said = session.text.rpartition("</think>")[2].strip()
        if refused:
            # Stated by the code, not left to the agent: an agent told no
            # often says nothing, and silence was being retold as success.
            return (f"NOT done: the person just said «no» to: "
                    f"{'; '.join(refused)}. {said}").strip()
        return said or "The agent finished and said nothing; whether it was done is unknown."

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
