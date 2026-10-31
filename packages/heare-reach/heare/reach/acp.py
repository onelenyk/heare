"""A client for any ACP agent: ask and wait, or hand a job off and live on.

ACP (Agent Client Protocol) is JSON-RPC 2.0, one message per line, over
the agent's stdin and stdout. Claude Code (through its adapter) and
opencode (natively) speak the same messages, so which agent answers is
one line in ``AGENTS`` and nothing below knows the difference.

Three things a voice needs come from the protocol itself, not from here:
the reply streams while it is written, so speech can start at the first
sentence; the agent asks before it acts (``session/request_permission``);
and a running turn can be stopped (``session/cancel``).

    agent = Agent("claude", on_permission=decide)
    await agent.start()
    reply = await agent.ask("що жере ресурси?")      # wait for the answer
    job = await agent.do("опублікуй чотири додатки")  # returns at once
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import os
import re
import signal
import time
from heare.core.i18n import _
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

AGENTS: dict[str, list[str]] = {
    "claude": ["npx", "-y", "@agentclientprotocol/claude-agent-acp@0.81.2"],
    "opencode": ["opencode", "acp"],
}

# Every call but a prompt gets an answer or a reason within this. A prompt
# is bounded by the hands' watchdog: it is meant to think for minutes.
CALL_TIMEOUT_S = 60.0

# Sent once, as the first block of a session's first prompt. The same text
# for every agent: ACP has no system-prompt field, and a prompt block is
# the one place every agent is guaranteed to read.
VOICE_RULES = (
    "You answer by voice: your text will be read aloud. Speak briefly, in one "
    "or two sentences, as a person would, in the language of the request. No "
    "markdown, lists, tables or code. If you do something long, say at the end "
    "in one sentence what came of it. If the work needs the person's word — a "
    "path, a choice, a detail you do not know — call the {ask} tool and wait "
    "for the answer; do not guess."
)


def ask_tool(agent: str) -> str:
    """How this CLI spells a tool from our MCP server «heare»."""
    return "mcp__heare__ask" if agent == "claude" else "heare_ask"

# The end of a first sentence: terminal punctuation followed by a space or
# the end of the text so far, or a line break.
_SENTENCE_END = re.compile(r"[.!?…](\s|$)|\n")


class AcpError(RuntimeError):
    """The agent answered a request with an error, or went away."""


@dataclass
class Event:
    """One thing that happened in a turn, as a voice would narrate it.

    ``kind`` is ``text`` (a piece of the reply), ``thought``, ``tool`` (the
    agent started doing something; ``text`` is its title), ``tool_done``
    or ``plan``. ``t`` is seconds since the prompt was sent.
    """

    kind: str
    text: str
    t: float


# The raw input fields that say what a tool is doing, in the order worth
# trying: a command, then a file, then a search.
_RAW = ("command", "file_path", "filePath", "path", "pattern", "query")


def _tool_text(u: dict) -> str:
    """A tool step in one line: its title, and what it is doing if it says."""
    title = u.get("title") or u.get("kind") or "tool"
    raw = u.get("rawInput")
    raw = raw if isinstance(raw, dict) else {}
    detail = next(
        (" ".join(str(raw[k]).split()) for k in _RAW
         if isinstance(raw.get(k), str) and raw[k].strip()),
        "",
    )
    if not detail:
        return title
    text = f"{title} · {detail}"
    # The 120 counts the ellipsis too: a step that fits is not cut.
    return text[:119] + "…" if len(text) > 120 else text


# Given the session and the request's params, answer "allow" or "reject".
Decide = Callable[["Session", dict], Awaitable[str]]


@dataclass
class Session:
    """One conversation with the agent. Its memory is the agent's own.

    Holds the state of the turn in flight: the reply so far, its events,
    and the three timings that decide how it feels by ear.
    """

    agent: Agent
    id: str
    options: dict[str, list[str]] = field(default_factory=dict)
    on_event: Callable[[Event], None] | None = None

    status: str = "idle"  # running · done · cancelled · failed
    stop_reason: str | None = None
    text: str = ""
    events: list[Event] = field(default_factory=list)
    first_text: float | None = None
    first_sentence: float | None = None
    total: float | None = None
    task: asyncio.Task | None = None

    _started: float = 0.0
    _turns: int = 0
    _asking: set[asyncio.Task] = field(default_factory=set)

    async def prompt(self, text: str) -> str:
        """Send one prompt and wait for the turn to end. Returns the reply."""
        self.text, self.events, self.stop_reason = "", [], None
        self.first_text = self.first_sentence = self.total = None
        self.status = "running"
        blocks = [{"type": "text", "text": text}]
        if self._turns == 0:
            blocks.insert(0, {"type": "text", "text": VOICE_RULES.format(
                ask=ask_tool(self.agent.name))})
        self._turns += 1
        self._started = time.monotonic()
        try:
            result = await self.agent._call(
                "session/prompt", {"sessionId": self.id, "prompt": blocks}
            )
            if not isinstance(result, dict):
                raise AcpError(f"{self.agent.name} answered a prompt with "
                               "something ACP does not have")
            self.stop_reason = result.get("stopReason")
            self.status = "cancelled" if self.stop_reason == "cancelled" else "done"
        except BaseException:
            self.status = "failed"
            raise
        finally:
            self.total = time.monotonic() - self._started
        return self.text

    async def cancel(self) -> None:
        """Stop the turn. Questions still waiting for a yes/no are withdrawn."""
        await self.agent._notify("session/cancel", {"sessionId": self.id})
        for ask in list(self._asking):
            ask.cancel()

    async def set(self, option: str, value: str) -> bool:
        """Set a session option the agent offered (``model``, ``mode``…).

        Returns False, and changes nothing, when the agent does not offer
        this option or this value.
        """
        if value not in self.options.get(option, ()):
            return False
        await self.agent._call(
            "session/set_config_option",
            {"sessionId": self.id, "configId": option, "value": value},
        )
        return True

    def _update(self, u: dict) -> None:
        if not isinstance(u, dict):
            return
        kind = u.get("sessionUpdate")
        t = time.monotonic() - self._started
        content = u.get("content")
        content = content if isinstance(content, dict) else {}
        if kind == "agent_message_chunk":
            chunk = content.get("text") or ""
            if not chunk:
                return
            # A new message after a tool call starts without a space; heard
            # aloud, "файл.Ви" is two sentences run into one.
            if (
                self.events
                and self.events[-1].kind != "text"
                and self.text[-1:].strip()
            ):
                self.text += " "
            self.text += chunk
            if self.first_text is None:
                self.first_text = t
            if self.first_sentence is None and _SENTENCE_END.search(self.text):
                self.first_sentence = t
            ev = Event("text", chunk, t)
        elif kind == "agent_thought_chunk":
            ev = Event("thought", content.get("text") or "", t)
        elif kind == "tool_call":
            ev = Event("tool", _tool_text(u), t)
        elif kind == "tool_call_update" and u.get("status") in ("completed", "failed"):
            ev = Event("tool_done", f"{u.get('title') or ''} {u['status']}".strip(), t)
        elif kind == "plan":
            entries = u.get("entries")
            entries = entries if isinstance(entries, list) else []
            steps = [e.get("content", "") for e in entries if isinstance(e, dict)]
            ev = Event("plan", " · ".join(steps), t)
        else:
            return
        self.events.append(ev)
        if self.on_event:
            self.on_event(ev)


class Agent:
    """One agent process, any number of sessions over it."""

    def __init__(
        self,
        name: str,
        *,
        on_permission: Decide,
        cwd: str | Path | None = None,
        mcp_servers: list[dict] | None = None,
        stderr: str | Path | None = None,
        command: list[str] | None = None,
        env: dict[str, str] | None = None,
        tap: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.name = name
        self.command = command or AGENTS[name]
        self._env = env
        self._tap = tap
        self.cwd = str(Path(cwd or Path.home()).resolve())
        self.mcp_servers = mcp_servers or []
        self.info: dict = {}
        self.startup: float | None = None
        self._decide = on_permission
        self._stderr = stderr
        self._stderr_file = None
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._sessions: dict[str, Session] = {}
        self._ask_session: Session | None = None

    # -- lifecycle ------------------------------------------------------

    @property
    def alive(self) -> bool:
        """True while the CLI is still there to answer."""
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        t0 = time.monotonic()
        Path(self.cwd).mkdir(parents=True, exist_ok=True)
        self._stderr_file = open(self._stderr, "ab") if self._stderr else None
        err = self._stderr_file or asyncio.subprocess.DEVNULL
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=err,
                env=self._env,
                cwd=self.cwd,
                start_new_session=True,  # a stop takes the whole tree, not just npx
                limit=2**24,  # one message is one line; tool output can be long
            )
            self._reader = asyncio.create_task(self._read(), name=f"acp-{self.name}")
            self.info = await self._call(
                "initialize",
                {
                    "protocolVersion": 1,
                    # No file or terminal access is lent to the agent: it uses its
                    # own tools, so the client stays a voice and nothing more.
                    "clientCapabilities": {
                        "fs": {"readTextFile": False, "writeTextFile": False},
                        "terminal": False,
                    },
                    "clientInfo": {"name": "heare", "version": "0"},
                },
            )
        except BaseException:
            await self.close()
            raise
        self.startup = time.monotonic() - t0

    async def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            self._signal_group(proc, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except TimeoutError:
                self._signal_group(proc, signal.SIGKILL)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(proc.wait(), 2)
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader
            self._reader = None
        self._fail_pending(AcpError(f"{self.name} closed"))
        if self._stderr_file is not None:
            with contextlib.suppress(OSError):
                self._stderr_file.close()
            self._stderr_file = None

    @staticmethod
    def _signal_group(proc: asyncio.subprocess.Process, sig: int) -> None:
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            with contextlib.suppress(ProcessLookupError):
                proc.send_signal(sig)

    # -- the three verbs' agent half -------------------------------------

    async def session(self, on_event: Callable[[Event], None] | None = None) -> Session:
        result = await self._call(
            "session/new", {"cwd": self.cwd, "mcpServers": self.mcp_servers}
        )
        options = {
            o["id"]: [v.get("value") for v in o.get("options") or []]
            for o in result.get("configOptions") or []
        }
        s = Session(self, result["sessionId"], options, on_event)
        self._sessions[s.id] = s
        return s

    async def reopened(
        self, session_id: str,
        on_event: Callable[[Event], None] | None = None,
    ) -> Session:
        """A session from an earlier process, continued where it stopped.
        One this process still holds is simply used again."""
        if session_id in self._sessions:
            held = self._sessions[session_id]
            held.on_event = on_event
            return held
        result = await self._call("session/resume", {
            "sessionId": session_id, "cwd": self.cwd,
            "mcpServers": self.mcp_servers,
        })
        options = {
            o["id"]: [v.get("value") for v in o.get("options") or []]
            for o in result.get("configOptions") or []
        }
        s = Session(self, session_id, options, on_event)
        self._sessions[s.id] = s
        return s

    async def ask(
        self, text: str, on_event: Callable[[Event], None] | None = None
    ) -> Session:
        """Ask and wait. Every ask shares one session, so a follow-up
        ("а другий лист?") has the context of the question before it."""
        if self._ask_session is None:
            self._ask_session = await self.session()
        s = self._ask_session
        s.on_event = on_event
        await s.prompt(text)
        return s

    async def do(
        self, text: str, on_event: Callable[[Event], None] | None = None
    ) -> Session:
        """Hand a job off. Returns at once; the job runs in its own session,
        so several can run side by side and none blocks an ask."""
        s = await self.session(on_event)
        s.task = asyncio.create_task(s.prompt(text), name=f"do-{s.id[:8]}")
        return s

    # -- JSON-RPC ---------------------------------------------------------

    async def _send(self, msg: dict) -> None:
        assert self._proc and self._proc.stdin
        self._proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode())
        await self._proc.stdin.drain()
        self._tapped("out", msg)

    def _tapped(self, direction: str, msg: dict) -> None:
        """Show the wire to whoever watches, without ever breaking it."""
        if self._tap is None:
            return
        try:
            self._tap(direction, msg)
        except Exception:  # noqa: BLE001 — a watcher's failure is not the agent's
            pass

    async def _call(
        self, method: str, params: dict, timeout: float | None = None
    ) -> dict:
        rid = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await self._send(
                {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
            )
        except BaseException:
            self._pending.pop(rid, None)
            raise
        if method == "session/prompt":
            return await fut
        limit = CALL_TIMEOUT_S if timeout is None else timeout
        try:
            return await asyncio.wait_for(asyncio.shield(fut), limit)
        except TimeoutError:
            self._pending.pop(rid, None)
            fut.cancel()
            raise AcpError(
                f"{self.name} did not answer {method} in {limit:g} s") from None

    async def _notify(self, method: str, params: dict) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _fail_pending(self, error: AcpError) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(error)
        self._pending.clear()

    async def _read(self) -> None:
        assert self._proc and self._proc.stdout
        try:
            async for line in self._proc.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue  # an agent's stray log line, not a message
                if not isinstance(msg, dict):
                    continue  # a frame of a shape ACP does not have
                self._tapped("in", msg)
                method = msg.get("method")
                if method and "id" in msg:
                    asyncio.create_task(self._answer(msg))
                elif method == "session/update":
                    p = msg.get("params")
                    if not isinstance(p, dict):
                        continue
                    s = self._sessions.get(p.get("sessionId"))
                    if s:
                        s._update(p.get("update"))
                elif not method:
                    fut = self._pending.pop(msg.get("id"), None)
                    if fut and not fut.done():
                        if "error" in msg:
                            fut.set_exception(AcpError(msg["error"]))
                        else:
                            fut.set_result(msg.get("result"))
        finally:
            self._fail_pending(AcpError(f"{self.name} exited"))

    async def _answer(self, msg: dict) -> None:
        """A request from the agent. Only permission is supported: the
        client offered no files and no terminal at ``initialize``."""
        rid, method = msg.get("id"), msg.get("method")
        p = msg.get("params")
        if method != "session/request_permission":
            if rid is not None:
                await self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "error": {"code": -32601, "message": "not offered"},
                    }
                )
            return
        if not isinstance(p, dict):
            p = {}
        s = self._sessions.get(p.get("sessionId"))
        ask = asyncio.create_task(self._decide(s, p)) if s else None
        outcome: dict = {"outcome": "cancelled"}
        if ask:
            s._asking.add(ask)
            try:
                verdict = await ask
            except asyncio.CancelledError:
                verdict = None  # the turn was cancelled while it waited
            except Exception:
                verdict = None  # a desk that fails says no, not hangs the turn
            finally:
                s._asking.discard(ask)
            if verdict:
                options = p.get("options")
                option = _pick(options if isinstance(options, list) else [], verdict)
                if option:
                    outcome = {"outcome": "selected", "optionId": option}
        if rid is not None:
            await self._send(
                {"jsonrpc": "2.0", "id": rid, "result": {"outcome": outcome}}
            )


def _pick(options: list[dict], verdict: str) -> str | None:
    """The option matching "allow" or "reject", preferring the one-time kind:
    a yes said aloud is about this action, not a standing rule."""
    prefix = "allow" if verdict == "allow" else "reject"
    kinds = [f"{prefix}_once", f"{prefix}_always"]
    for kind in kinds:
        for o in options:
            if not isinstance(o, dict):
                continue
            option_id = o.get("optionId")
            if o.get("kind") == kind and isinstance(option_id, str):
                return option_id
    return None


def describe(request: dict) -> str:
    """A permission request in one line: what the agent wants to do."""
    call = request.get("toolCall") or {}
    title = call.get("title") or call.get("kind") or _("something")
    raw = call.get("rawInput") or {}
    detail = raw.get("command") or raw.get("file_path") or raw.get("path") or ""
    return f"{title} {detail}".strip() if detail and detail not in title else title
