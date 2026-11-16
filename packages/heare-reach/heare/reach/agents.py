"""opencode as the executor: one agent, whichever model it runs.

The assistant does not do the work. It knows who can, hands the task
over, shows what they are doing, asks the person before anything is
changed, and stops them when told to. The agent is opencode: a server of
its own, spoken to over HTTP (``heare.reach.opencode``).

Reading is free; a change is asked about. The question goes to the core
(``desk.permit``), which asks the person and catches the answer in code.
An agent that does not ask cannot be made to from here, so it is started
with a configuration that makes it ask (see ``_opencode_env``), and the
answer is given back to it one act at a time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from heare.reach import opencode, runner
from heare.core import Desk
from heare.core.i18n import N_, _
from heare.reach.life import LifeOver, guarded

logger = logging.getLogger("heare.reach")

# How long an agent may say nothing before it is called stuck, and how long one
# task may run in total. A coder thinks for minutes, so idle is generous.
IDLE_S = 5 * 60.0
TOTAL_S = 30 * 60.0
# A server that cannot even say hello is not started; a stopped command gets
# this long to withdraw before its whole process group is killed.
START_S = 60.0
STOP_GRACE_S = 5.0
STOP_POLL_S = 0.2

# What an agent may do without asking: looking is not changing.
_READING = frozenset({"read", "search", "think", "fetch"})
# A request, as a person would say it. An agent's own title is often
# just a path or a command line.
_DOING = {
    "edit": N_("change the file"), "delete": N_("delete"), "move": N_("move"),
    "execute": N_("run the command"),
}


# A command's setup is not the act: the person is owed the command, not the
# environment wrapped around it.
_SETUP = re.compile(r"^(?:export\s+)?(?:[A-Za-z_][A-Za-z0-9_]*=\S+(?:\s+|$))+")
# Said, not printed: the screen has the whole command.
_COMMAND_CHARS = 60


def _command_said(command: str) -> str:
    """The act, without its setup, short enough to say aloud."""
    said = " ".join(_SETUP.sub("", command).split()) or " ".join(command.split())
    return said if len(said) <= _COMMAND_CHARS \
        else said[:_COMMAND_CHARS - 3] + "…"


def _where(raw: dict) -> str:
    """The path an action names, however this release of opencode spells it."""
    for key in ("command", "file_path", "filePath", "filepath", "path"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _what(request: dict) -> str:
    """What the agent wants to do, in words fit to be said aloud."""
    call = request.get("toolCall")
    call = call if isinstance(call, dict) else {}
    raw = call.get("rawInput")
    raw = raw if isinstance(raw, dict) else {}
    command = raw.get("command")
    target = str(command or _where(raw) or call.get("title") or "")
    if command:
        target = _command_said(str(target))
    elif "/" in target and " " not in target:
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


# The model is chosen per session, not in the config: a fresh server has its
# own default, and the session is where one says otherwise. A model this
# server does not have is said on the card, not silently ignored.
MODEL = "deepseek/deepseek-flash"


# How much the agent does on its own (the owner's choice, 24 October):
# «careful» brings every change to the person; «normal» lets the judge in
# the brain weigh each one against what the person asked; «auto» asks about
# nothing. Every mode refuses an action that names an admin tool; the check
# is a literal word scan of the action, so an obfuscated command
# (`sh -c "$(printf su)do …"`) can slip past it — `auto` has no other gate.
MODES = ("careful", "normal", "auto")
MODE = "normal"

# Never, in any mode.
_NEVER = (
    "sudo", "su", "security", "defaults write", "defaults delete", "systemsetup",
    "networksetup", "pmset", "spctl", "csrutil", "nvram", "diskutil", "launchctl",
    "dscl", "passwd", "chsh", "fdesetup", "tccutil",
)
# The same floor as words rather than prefix patterns: an absolute path or a
# wrapper (`sh -c 'sudo …'`) still names the act, and `su` must not match
# `sudo` or a path that merely contains those letters.
_NEVER_WORDS = frozenset(pattern.split()[0] for pattern in _NEVER)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


def _never(text: str) -> str | None:
    """The forbidden word an action names, if any, wherever it is spelled."""
    found = _NEVER_WORDS.intersection(_WORD.findall(text))
    return min(found) if found else None


# opencode's own tools that only look: never worth a question, nor a judge.
# The `question` tool is a question to the person already; weighing it first
# asked twice (24 October).
_LOOKING_TOOLS = ("read", "glob", "grep", "list", "lsp", "todowrite", "todoread",
                  "webfetch", "websearch", "codesearch", "external_directory",
                  "question", "skill", "heare_show")

# Told to the agent with every task: a rule it cannot see is a rule it
# walks into.
_RULES = {
    "careful": "",
    "normal": ("[heare rules] Delete files to the Trash: `trash <path>`, not `rm`. "
               "What cannot be undone or will be seen by others is weighed "
               "first and may be put to the owner.\n\n"),
    "auto": "[heare rules] When deleting files, use `trash <path>`, not `rm`.\n\n",
}

# Said with the first prompt of a session: a voice has no room for markdown,
# and a question is the one thing the agent cannot settle on its own.
VOICE_RULES = (
    "You answer by voice: your text will be read aloud. Speak briefly, in one "
    "or two sentences, as a person would, in the language of the request. No "
    "markdown, lists, tables or code. If you do something long, say at the end "
    "in one sentence what came of it. If the work needs the person's word — a "
    "path, a choice, a detail you do not know — call the `question` tool and "
    "wait for the answer; do not guess."
)


def _commands(rule: str, names: tuple[str, ...]) -> dict[str, str]:
    return {pattern: rule for name in names for pattern in (name, f"{name} *")}


def _opencode_config(patch: dict | None = None, mode: str = MODE) -> dict:
    """Everything that is not plainly looking comes to `_decide`, MCP tools
    too (the catch-all `*`); there the mode decides who weighs it. Even in
    `auto` the wire asks first, because ``_decide`` is the one floor every
    agent crosses. The last pattern that matches wins, so each catch-all goes
    first."""
    permission: dict = {"*": "ask", **{tool: "allow" for tool in _LOOKING_TOOLS},
                        "edit": "ask", "bash": {"*": "ask", **_commands("allow", _LOOKING)}}
    permission["bash"].update(_commands("deny", _NEVER))
    config = {"$schema": "https://opencode.ai/config.json", "permission": permission}
    config.update(patch or {})
    return config


# Where the agent's own home is, under the machine's: a bug in 0.9.26 put it
# one level up, and the folders are adopted rather than lost.
_AGENT_FOLDERS = ("config", "data", "home", "state", "cache")


def _adopt_home(machine: Path) -> None:
    """Move a home that landed beside the agent's folder into it.

    In 0.9.26 the environment pointed one level up: the agent's config,
    sign-in, sessions and cache were written into the machine's own folder.
    Sessions and a sign-in are not worth losing to a path fix, so the folders
    are moved into the agent's home on the first run after the fix.
    """
    target = machine / "opencode"
    for name in _AGENT_FOLDERS:
        old = machine / name
        if not old.is_dir():
            continue
        # Only ours: the config, data, state and cache of opencode, and the
        # home the git identity was written into.
        if not ((old / "opencode").is_dir() or (old / ".gitconfig").exists()):
            continue
        # A folder of the same name may already be there from an older
        # version's own layout (0.9.24 kept its home in the same place), so
        # this merges rather than skips: only what is missing is moved, and
        # what is there wins.
        moved = 0
        try:
            for src in sorted(old.rglob("*")):
                if src.is_dir():
                    continue
                dst = target / name / src.relative_to(old)
                if dst.exists():
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                src.replace(dst)
                moved += 1
            shutil.rmtree(old, ignore_errors=True)
        except OSError:
            logger.warning("could not move %s into the agent's home", old)
        else:
            if moved:
                logger.info("moved %d files from %s into the agent's home", moved, old)


def _opencode_env(home: Path, patch: dict | None = None,
                  mode: str = MODE, base: dict[str, str] | None = None) -> dict[str, str]:
    """opencode with a configuration of the assistant's own, in which a
    write or a command that changes something is asked about. Measured on
    25 September: with its defaults it wrote a file after being told no,
    because it never asked. The person's own opencode configuration is
    not touched; this file is the assistant's and is rewritten to match.

    An inherited ``OPENCODE_CONFIG`` survives only when it is this very
    file — an agent may edit what it can write, but it may not redirect the
    permission config to its own."""
    config = home / "opencode"
    config.mkdir(parents=True, exist_ok=True)
    path = config / "opencode.json"
    merged = _opencode_config(patch, mode)
    # The person's own additions — providers, plugins, an MCP server — live
    # beside ours in extra.json and are merged in, except the permissions:
    # the floor under every act is ours and is not negotiable.
    try:
        extra = json.loads((config / "extra.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        extra = {}
    if isinstance(extra, dict):
        merged.update({key: value for key, value in extra.items()
                       if key != "permission"})
    wanted = json.dumps(merged, indent=2)
    try:
        current = path.read_text(encoding="utf-8")
    except OSError:
        current = ""
    if current != wanted:
        path.write_text(wanted, encoding="utf-8")
    env = dict(base) if base is not None else runner.env(
        "opencode", "login", "", "", "")
    _adopt_home(home)
    env = runner.agent_env(config, env)
    env["OPENCODE_CONFIG_DIR"] = str(config)
    parent = os.environ.get("OPENCODE_CONFIG")
    if parent and Path(parent).resolve() != path.resolve():
        env.pop("OPENCODE_CONFIG", None)
    env["OPENCODE_CONFIG"] = str(path)
    return env


class Agent:
    """opencode in the form the core asks of an executor."""

    def __init__(self, known: Known, *, cwd: str | Path | None = None,
                 home: Path | None = None, command: list[str] | None = None,
                 env: dict[str, str] | None = None,
                 model: str | None = None,
                 config_patch: dict | None = None,
                 tap: Callable[[str, dict], None] | None = None,
                 mode: str = MODE) -> None:
        self.name = known.name
        self.id = self.name
        self.mode = mode if mode in MODES else MODE
        self.about = known.about
        self._known = known
        self._cwd = Path(cwd).expanduser() if cwd else Path.home() / "heare-work"
        self._home = home or Path.home() / ".heare"
        self._command = command or list(known.command)
        self._env = env
        self._model = model
        self._config_patch = config_patch or {}
        self._tap = tap
        self._agent: opencode.Agent | None = None
        self._session: opencode.Session | None = None
        self._last: str = ""
        self._told: set[str] = set()
        self._desks: dict[str, Desk] = {}
        self._refused: dict[str, list[str]] = {}
        # Questions waiting for the person: the agent is not idle meanwhile,
        # however long they take.
        self._waiting = 0
        self._activity = time.monotonic()
        self._starting = asyncio.Lock()

    async def _started(self) -> opencode.Agent:
        """One server for every task, started when first needed — and a
        dead one is thrown away rather than reused."""
        async with self._starting:
            if self._agent is not None and not self._agent.alive:
                await self._agent.close()
                self._agent = None
            if self._agent is None:
                env = self._env
                if env is None:
                    env = runner.env(self.name, "login", "", "", "")
                if self.name == "opencode":
                    env = _opencode_env(
                        self._home / "machine", self._config_patch, self.mode, env)
                logs = self._home / "logs"
                logs.mkdir(parents=True, exist_ok=True)
                agent = opencode.Agent(
                    self.name, on_permission=self._decide, on_question=self._asked,
                    cwd=self._cwd,
                    stderr=logs / f"agent-{self.name}.log",
                    command=self._command, env=env, tap=self._tap,
                )
                try:
                    await asyncio.wait_for(agent.start(), START_S)
                except BaseException:
                    await agent.close()
                    raise
                self._agent = agent
            return self._agent

    async def _decide(self, session: opencode.Session, request: dict) -> str:
        desk = self._desks.get(session.id)
        if desk is None:
            return "reject"
        call = request.get("toolCall") or {}
        if not isinstance(call, dict):
            call = {}
        what = _what(request)
        # The judge is shown the action whole: which tool, and with what.
        detail = json.dumps({k: call.get(k) for k in ("kind", "title", "rawInput")
                             if call.get(k)}, ensure_ascii=False)
        # The floor is checked before the mode: `auto` may skip the person,
        # but it may not hand out root, keychain or system settings.
        if _never(detail):
            self._refused.setdefault(session.id, []).append(what)
            return "reject"
        if self.mode == "auto":
            return "allow"
        if call.get("kind") in _READING:
            kind = "read"
        else:
            kind = "judge" if self.mode == "normal" else "change"
        self._waiting += 1
        try:
            allowed = await desk.permit(what, kind, detail)
        finally:
            self._waiting -= 1
            self._activity = time.monotonic()
        if not allowed:
            self._refused.setdefault(session.id, []).append(what)
        return "allow" if allowed else "reject"

    async def _model_here(self, agent: opencode.Agent) -> str:
        """The model to ask for: ours when this home has it, the server's own
        otherwise. A model that is not here must not fail in silence."""
        wanted = self._model or MODEL
        try:
            found = await agent.call("GET", "/config/providers")
        except opencode.OpencodeError:
            return wanted
        names = {
            f"{provider.get('id')}/{model}"
            for provider in found.get("providers") or [] if isinstance(provider, dict)
            for model in (provider.get("models") or {})
        }
        if names and wanted not in names:
            logger.warning("%s: no model %s in its home; the server's own is used",
                           self.name, wanted)
            return ""
        return wanted

    async def _asked(self, session: opencode.Session, question: str) -> str:
        """The agent's own question, carried to the person and answered."""
        desk = self._desks.get(session.id)
        ask = getattr(desk, "ask", None)
        if ask is None:
            return ""
        self._waiting += 1
        try:
            return await ask(question)
        finally:
            self._waiting -= 1
            self._activity = time.monotonic()

    @property
    def last_session(self) -> str:
        return self._last

    async def changes(self) -> list[dict]:
        """What the last task changed, as the server sees it: one entry per
        file, with the patch. Empty when there is nothing to tell — a folder
        that is not a repository has no snapshot to compare with."""
        session, agent = self._session, self._agent
        if session is None or agent is None:
            return []
        try:
            message = await session.last_user_message()
            if not message:
                return []
            return await session.diff(message)
        except opencode.OpencodeError:
            return []

    async def undo(self) -> str:
        """Put the files back the way they were before the last task.

        The server keeps the snapshot; nothing is guessed. Work that changed
        nothing (a folder that is not a repository) has nothing to undo, and
        says so instead of pretending."""
        session, agent = self._session, self._agent
        if session is None or agent is None:
            return _("Nothing to undo.")
        try:
            if await session.reverted():
                return _("Nothing to undo.")
            message = await session.last_user_message()
            if not message or not await session.diff(message):
                return _("Nothing to undo.")
            await session.revert(message)
            if not await session.reverted():
                return _("Could not put it back.")
        except opencode.OpencodeError:
            return _("Could not put it back.")
        return _("Put it back the way it was.")

    async def redo(self) -> str:
        """Bring back what was undone: the change as it was."""
        session, agent = self._session, self._agent
        if session is None or agent is None:
            return _("Nothing to bring back.")
        try:
            await session.unrevert()
            if await session.reverted():
                return _("Could not bring it back.")
        except opencode.OpencodeError:
            return _("Could not bring it back.")
        return _("Brought the change back.")

    async def recent(self) -> list[dict]:
        """What the hands did lately, newest first: they know, not the brain."""
        try:
            agent = await self._started()
            return await agent.recent()
        except (opencode.OpencodeError, OSError):
            return []  # no server, or one that did not come up: no list

    async def branch(self, session_id: str = "") -> bool:
        """Go on in a branch of a session: the original stays as it was."""
        agent = await self._started()
        try:
            if session_id:
                self._session = await agent.reopened(session_id, None)
            session = self._session
            if session is None:
                return False
            branch = await session.fork()
        except opencode.OpencodeError:
            return False
        self._session, self._last = branch, branch.id
        return True

    async def todo(self) -> list[dict]:
        """The agent's own plan for the session it is in, as it stands now."""
        session, agent = self._session, self._agent
        if session is None or agent is None:
            return []
        try:
            return await session.todo()
        except opencode.OpencodeError:
            return []

    async def price(self) -> dict:
        """What the last task cost: the server keeps the bill per session."""
        agent = self._agent
        if agent is None or not self._last:
            return {}
        try:
            found = await agent.call("GET", f"/session/{self._last}")
        except opencode.OpencodeError:
            return {}
        return found if isinstance(found, dict) else {}

    async def run(self, task: str, desk: Desk, *, resume: str = "",
                  plan: bool = False) -> str:
        """Do the task. ``plan`` runs this turn as the agent that only looks:
        a plan, no changes, and the person's word decides what happens next."""
        agent = await self._started()

        def on_event(event: opencode.Event) -> None:
            self._activity = time.monotonic()
            if event.kind == "tool" and event.text:
                desk.step(event.text[:80])

        session = None
        if resume:
            try:
                session = await agent.reopened(resume, on_event)
            except opencode.OpencodeError:
                logger.info("%s: session %s is gone; a new one", self.name, resume)
        if session is None:
            session = await agent.session(on_event,
                                          model=await self._model_here(agent))
            resume = ""
        self._last = session.id
        self._session = session
        self._desks[session.id] = desk
        self._activity = time.monotonic()
        # A voice is told once per session: a resumed one has heard it.
        told = bool(resume) or session.id in self._told
        prompt_text = ("" if told else VOICE_RULES + "\n\n") + _RULES[self.mode] + task
        prompt = asyncio.create_task(
            session.prompt(prompt_text, agent="plan" if plan else ""),
            name=f"turn-{session.id[:8]}")
        watch = asyncio.create_task(
            self._called_off(desk, session, agent, prompt),
            name=f"stop-{session.id[:8]}")
        try:
            await guarded(prompt,
                          lambda: time.monotonic() if self._waiting else self._activity,
                          idle_s=IDLE_S, total_s=TOTAL_S, stop=session.cancel)
        except asyncio.CancelledError:
            # Being cancelled is how it is told to stop — and it must.
            try:
                await asyncio.shield(session.cancel())
            except Exception:  # noqa: BLE001
                logger.exception("%s: could not be told to stop", self.name)
            raise
        except LifeOver:
            # The turn was called stuck: the session is not enough, the server
            # itself must go so the next job starts clean.
            await self._retire(agent)
            raise
        except opencode.OpencodeError:
            if not getattr(desk, "stopped", False):
                raise
        finally:
            self._told.add(session.id)
            watch.cancel()
            await asyncio.gather(watch, return_exceptions=True)
            self._desks.pop(session.id, None)
            refused = self._refused.pop(session.id, [])
        if session.status == "cancelled" or getattr(desk, "stopped", False):
            return "Stopped."
        # The last message is the answer; the task's own chatter is not.
        # Some models leak their thinking into it; what follows the last
        # closing tag is what is said.
        said = (session.answer or session.text).rpartition("</think>")[2].strip()
        if refused:
            # Stated by the code, not left to the agent: an agent told no
            # often says nothing, and silence was being retold as success.
            return (f"NOT done: the person just said «no» to: "
                    f"{'; '.join(refused)}. {said}").strip()
        return said or "The agent finished and said nothing; whether it was done is unknown."

    async def _called_off(self, desk: Desk, session: opencode.Session,
                          agent: opencode.Agent, prompt: asyncio.Task) -> None:
        """Watch for «стоп» between events; a command that will not withdraw
        is not left running after the person has called it off."""
        while not prompt.done():
            await asyncio.sleep(STOP_POLL_S)
            if not getattr(desk, "stopped", False):
                continue
            try:
                await session.cancel()
            except Exception:  # noqa: BLE001 — a dead server is already stopped
                logger.exception("%s: could not be told to stop", self.name)
            try:
                await asyncio.wait_for(asyncio.shield(prompt), STOP_GRACE_S)
            except TimeoutError:
                await self._retire(agent)
            except Exception:  # noqa: BLE001 — the turn ended on its own
                pass
            return

    async def _retire(self, agent: opencode.Agent) -> None:
        if self._agent is agent:
            self._agent = None
        await agent.close()

    async def close(self) -> None:
        if self._agent is not None:
            await self._agent.close()
            self._agent = None


def connect(settings: Any) -> list[Agent]:
    """The agent the settings name (``executor``; «all» for every
    installed one), working in its own folder (``executor_cwd``)."""
    only = str(getattr(settings, "executor", "") or "").strip().lower()
    cwd = Path(getattr(settings, "executor_cwd", "")
               or Path.home() / "heare-work").expanduser()
    cwd.mkdir(parents=True, exist_ok=True)
    found = [k for k in installed() if only in ("", "all") or k.name == only]
    return [Agent(k, cwd=cwd) for k in found]
