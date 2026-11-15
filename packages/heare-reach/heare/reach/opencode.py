"""opencode, the one agent: one server, one client, one stream.

`opencode serve --port N` is opencode's own server — the same one its TUI
talks to — and everything the assistant needs is there: a session, a
prompt that streams while it is written, a permission asked before an act,
a stop, and what ACP never showed: the diff of a task, the undo of it, the
todo list, the price.

    agent = Agent(on_permission=decide, cwd=work)
    await agent.start()
    session = await agent.session(model="deepseek/deepseek-flash")
    reply = await session.prompt("що жере ресурси?")

Three things come from the protocol itself: the reply streams
(`message.part.delta`), the agent asks before it acts
(`permission.asked`), and a running turn can be stopped
(`POST /session/{id}/abort`). A question from the agent arrives the same
way (`question.asked`), so nothing of ours has to stand in front of it.

Only the classic `/session/*` calls are used — the ones opencode's own
interface calls. `/api/*` and `/experimental/*` move between releases, so
they are not a foundation.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import signal
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

# A server that cannot say hello in this long is not started. A prompt is
# bounded by the hands' watchdog instead: it is meant to think for minutes.
START_S = 60.0
CALL_S = 60.0
# The end of a first sentence: terminal punctuation followed by a space or
# the end of the text so far, or a line break.
_SENTENCE_END = re.compile(r"[.!?…](\s|$)|\n")

# What a permission call is called in words fit to be said aloud.
_KINDS = {"bash": "execute", "edit": "edit", "write": "edit", "read": "read",
          "webfetch": "fetch", "question": "think"}


logger = logging.getLogger("heare.reach")


class OpencodeError(RuntimeError):
    """The server answered with an error, or went away."""


@dataclass
class Event:
    """One thing that happened in a turn, as a voice would narrate it.

    ``kind`` is ``text`` (a piece of the reply), ``thought``, ``tool`` (the
    agent started doing something; ``text`` is its title), ``tool_done`` or
    ``plan``. ``t`` is seconds since the prompt was sent.
    """

    kind: str
    text: str
    t: float


# Given the session and the request, answer "allow" or "reject".
Decide = Callable[["Session", dict], Awaitable[str]]
Asked = Callable[["Session", str], Awaitable[str]]


def free_port() -> int:
    """A port nothing is listening on, for a server about to be started."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def split_model(model: str) -> dict[str, str]:
    """``provider/model`` as the server spells it, or {} when it is not one."""
    provider, sep, name = model.partition("/")
    if not sep or not provider or not name:
        return {}
    return {"providerID": provider, "id": name}


def _tool_text(part: dict) -> str:
    """A tool step in one line: what it is, and what it is doing if it says."""
    state = part.get("state") or {}
    state = state if isinstance(state, dict) else {}
    data = state.get("input") or {}
    data = data if isinstance(data, dict) else {}
    detail = next(
        (" ".join(str(data[key]).split()) for key in
         ("command", "filePath", "filepath", "path", "pattern", "query")
         if isinstance(data.get(key), str) and data[key].strip()),
        "",
    )
    title = str(part.get("tool") or "tool")
    text = f"{title} · {detail}" if detail else title
    # The 120 counts the ellipsis too: a step that fits is not cut.
    return text[:119] + "…" if len(text) > 120 else text


@dataclass
class Session:
    """One conversation with the server. Its memory is the server's own."""

    agent: Agent
    id: str
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
    _parts: dict[str, str] = field(default_factory=dict)
    _order: list[str] = field(default_factory=list)
    # Parts by the message they belong to: a task says a lot on the way,
    # and only the last message is the answer.
    _messages: dict[str, dict[str, str]] = field(default_factory=dict)
    _messages_order: list[str] = field(default_factory=list)
    _roles: dict[str, str] = field(default_factory=dict)
    _called: set[str] = field(default_factory=set)
    _finished: set[str] = field(default_factory=set)
    _idle: asyncio.Event = field(default_factory=asyncio.Event)
    _cancelled: bool = False

    async def prompt(self, text: str, agent: str = "") -> str:
        """Send one prompt and wait for the turn to end. Returns the reply.

        ``agent`` names the agent this turn runs as (``plan`` is the one that
        only looks); an empty one leaves the session's own choice."""
        self.text, self.events, self.stop_reason = "", [], None
        self._parts.clear()
        self._order.clear()
        self._messages.clear()
        self._messages_order.clear()
        self._called.clear()
        self._finished.clear()
        self.first_text = self.first_sentence = None
        self._cancelled = False
        self.status = "running"
        self._idle.clear()
        self._started = time.monotonic()
        try:
            body: dict[str, Any] = {"parts": [{"type": "text", "text": text}]}
            if agent:
                body["agent"] = agent
            await self.agent.call(
                "POST", f"/session/{self.id}/prompt_async", body)
            await self._idle.wait()
            self.status = "cancelled" if self._cancelled else "done"
        except BaseException:
            self.status = "failed"
            raise
        finally:
            self.total = time.monotonic() - self._started
        return self.text

    @property
    def answer(self) -> str:
        """What the agent said last: the words of the last message it wrote.

        A coding task talks on the way («I'll look at the files», «fixing
        calc.py»); what is worth hearing is the end."""
        for message in reversed(self._messages_order):
            parts = self._messages.get(message) or {}
            said = "".join(parts.values()).strip()
            if said:
                return said
        return ""

    async def cancel(self) -> None:
        """Tell the server to stop the turn. The server says when it is over;
        a server that will not stop is retired, not waited on."""
        self._cancelled = True
        with contextlib.suppress(OpencodeError):
            await self.agent.call("POST", f"/session/{self.id}/abort")

    def stopped(self) -> None:
        """The server is gone: whatever turn was running cannot go on."""
        self._cancelled = True
        self._idle.set()

    # -- what only the server can tell ------------------------------------

    async def diff(self, message: str = "") -> list[dict]:
        """The files a message changed: ``{file, patch, additions, …}``."""
        params = {"messageID": message} if message else None
        found = await self.agent.call(
            "GET", f"/session/{self.id}/diff", params=params)
        return found if isinstance(found, list) else []

    async def todo(self) -> list[dict]:
        """The agent's own plan for this session, as it stands."""
        found = await self.agent.call("GET", f"/session/{self.id}/todo")
        return found if isinstance(found, list) else []

    async def reverted(self) -> dict:
        """The boundary the session stands rolled back to, or {} when it
        stands as it was. The server keeps this, so it survives a restart."""
        info = await self.agent.call("GET", f"/session/{self.id}")
        found = info.get("revert") if isinstance(info, dict) else None
        return found if isinstance(found, dict) else {}

    async def revert(self, message: str) -> None:
        """Put the files back the way they were before *message*."""
        await self.agent.call(
            "POST", f"/session/{self.id}/revert", {"messageID": message})

    async def unrevert(self) -> None:
        """Undo the undo: the reverted messages come back."""
        await self.agent.call("POST", f"/session/{self.id}/unrevert")

    async def fork(self) -> "Session":
        """A branch of this session: the same history, its own future."""
        found = await self.agent.call("POST", f"/session/{self.id}/fork")
        if not isinstance(found, dict) or not found.get("id"):
            raise OpencodeError(f"{self.agent.name} made no fork: {found}")
        branch = Session(self.agent, str(found["id"]))
        self.agent._sessions[branch.id] = branch
        return branch

    async def last_user_message(self) -> str:
        """The message the last prompt made: the boundary of one task's work."""
        found = await self.agent.call("GET", f"/session/{self.id}/message")
        if not isinstance(found, list):
            return ""
        for message in reversed(found):
            info = message.get("info") or {}
            if info.get("role") == "user":
                return str(info.get("id") or "")
        return ""

    # -- what the stream says ---------------------------------------------

    def _end_turn(self) -> None:
        self._idle.set()

    def _mine(self, message_id: str) -> bool:
        """A part of the agent's own words — not the person's, read back."""
        return self._roles.get(message_id, "assistant") == "assistant"

    def _part(self, part: dict) -> None:
        if not isinstance(part, dict):
            return
        t = time.monotonic() - self._started
        kind = part.get("type")
        if kind == "text":
            if not self._mine(str(part.get("messageID") or "")):
                return
            self._say(str(part.get("id") or ""), str(part.get("text") or ""), t,
                      str(part.get("messageID") or ""))
        elif kind == "reasoning":
            said = str(part.get("text") or "")
            if said and said != self._parts.get(str(part.get("id") or "")):
                self._parts[str(part.get("id") or "")] = said
                self._emit(Event("thought", said, t))
        elif kind == "tool":
            self._tool(part, t)

    def _delta(self, update: dict) -> None:
        """A piece of a part as it is written. Only the text is kept — the
        full part arrives beside it, and one truth is enough."""
        if update.get("field") != "text":
            return
        if not self._mine(str(update.get("messageID") or "")):
            return
        self._say(str(update.get("partID") or ""),
                  self._parts.get(str(update.get("partID") or ""), "")
                  + str(update.get("delta") or ""),
                  time.monotonic() - self._started,
                  str(update.get("messageID") or ""))

    def _say(self, part_id: str, text: str, t: float, message: str = "") -> None:
        before = self._parts.get(part_id, "")
        if text == before:
            return
        delta = text[len(before):] if text.startswith(before) else text
        self._parts[part_id] = text
        if part_id not in self._order:
            self._order.append(part_id)
        if message:
            if message not in self._messages_order:
                self._messages_order.append(message)
            self._messages.setdefault(message, {})[part_id] = text
        self.text = "".join(self._parts[key] for key in self._order)
        if self.first_text is None:
            self.first_text = t
        if self.first_sentence is None and _SENTENCE_END.search(self.text):
            self.first_sentence = t
        self._emit(Event("text", delta, t))

    def _tool(self, part: dict, t: float) -> None:
        call = str(part.get("callID") or part.get("id") or "")
        status = str((part.get("state") or {}).get("status") or "")
        if status in ("pending", "running") and call not in self._called:
            self._called.add(call)
            self._emit(Event("tool", _tool_text(part), t))
        elif status == "completed" and call not in self._finished:
            self._finished.add(call)
            self._emit(Event("tool_done", _tool_text(part), t))
        elif status == "error" and call not in self._finished:
            self._finished.add(call)
            self._emit(Event("tool_done", _tool_text(part), t))

    def _emit(self, event: Event) -> None:
        self.events.append(event)
        if self.on_event:
            self.on_event(event)


class Agent:
    """One opencode server, any number of sessions over it."""

    def __init__(
        self,
        name: str = "opencode",
        *,
        on_permission: Decide,
        on_question: Asked | None = None,
        cwd: str | Path | None = None,
        stderr: str | Path | None = None,
        command: list[str] | None = None,
        env: dict[str, str] | None = None,
        tap: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.name = name
        self.command = command or ["opencode", "serve"]
        self._env = env
        self._tap = tap
        self.cwd = str(Path(cwd or Path.home()).resolve())
        self.info: dict = {}
        self.startup: float | None = None
        self.port = 0
        self._decide = on_permission
        self._asked = on_question
        self._stderr = stderr
        self._stderr_file = None
        self._proc: asyncio.subprocess.Process | None = None
        self._client: httpx.AsyncClient | None = None
        self._stream: asyncio.Task | None = None
        self._sessions: dict[str, Session] = {}

    # -- lifecycle ------------------------------------------------------

    @property
    def alive(self) -> bool:
        """True while the server is still there to answer."""
        return self._proc is not None and self._proc.returncode is None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        t0 = time.monotonic()
        Path(self.cwd).mkdir(parents=True, exist_ok=True)
        self.port = free_port()
        self._stderr_file = open(self._stderr, "ab") if self._stderr else None
        err = self._stderr_file or asyncio.subprocess.DEVNULL
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self.command, "--port", str(self.port),
                "--hostname", "127.0.0.1",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=err,
                env=self._env,
                cwd=self.cwd,
                start_new_session=True,  # a stop takes the whole tree
                limit=2**24,
            )
            self._client = httpx.AsyncClient(
                base_url=self.url,
                timeout=httpx.Timeout(CALL_S, connect=5.0),
            )
            self.info = await self._healthy()
            self._stream = asyncio.create_task(
                self._events(), name=f"opencode-{self.port}")
        except BaseException:
            await self.close()
            raise
        self.startup = time.monotonic() - t0

    async def close(self) -> None:
        for session in self._sessions.values():
            session.stopped()
        proc, self._proc = self._proc, None
        if self._stream is not None:
            self._stream.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._stream
            self._stream = None
        if self._client is not None:
            client, self._client = self._client, None
            with contextlib.suppress(Exception):
                await client.aclose()
        if proc is not None and proc.returncode is None:
            self._signal_group(proc, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except TimeoutError:
                self._signal_group(proc, signal.SIGKILL)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(proc.wait(), 2)
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

    async def _healthy(self) -> dict:
        """Wait for the server to say it is up; a CLI that dies is an error."""
        deadline = time.monotonic() + START_S
        last = ""
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.returncode is not None:
                raise OpencodeError(
                    f"{self.name} exited before it was ready ({last or 'no answer'})")
            try:
                answer = await self._http("GET", "/global/health")
            except (httpx.HTTPError, OpencodeError, OSError) as exc:
                last = str(exc)
                await asyncio.sleep(0.2)
                continue
            if isinstance(answer, dict) and answer.get("healthy"):
                return answer
            last = str(answer)
            await asyncio.sleep(0.2)
        raise OpencodeError(f"{self.name} did not come up in {int(START_S)} s")

    # -- the three verbs -------------------------------------------------

    async def session(
        self,
        on_event: Callable[[Event], None] | None = None,
        *,
        model: str = "",
        agent: str = "",
    ) -> Session:
        body: dict[str, Any] = {}
        if model:
            chosen = split_model(model)
            if chosen:
                body["model"] = chosen
        if agent:
            body["agent"] = agent
        try:
            result = await self.call("POST", "/session", body)
        except OpencodeError:
            if "model" not in body:
                raise
            # A model this home does not have is worth saying, not worth
            # stopping the work for: the server's own choice will do.
            logger.warning("%s: no model %s here; the default is used",
                           self.name, body.get("model"))
            body.pop("model", None)
            result = await self.call("POST", "/session", body)
        if not isinstance(result, dict) or not result.get("id"):
            raise OpencodeError(f"{self.name} made no session: {result}")
        session = Session(self, str(result["id"]), on_event)
        self._sessions[session.id] = session
        return session

    async def recent(self, limit: int = 10) -> list[dict]:
        """The work done in this folder, newest first: id, title, when."""
        found = await self.call(
            "GET", "/session", params={"directory": self.cwd})
        items = [item for item in found if isinstance(item, dict)] \
            if isinstance(found, list) else []
        items.sort(key=lambda item: ((item.get("time") or {}).get("updated") or 0),
                   reverse=True)
        return items[:limit]

    async def reopened(
        self,
        session_id: str,
        on_event: Callable[[Event], None] | None = None,
    ) -> Session:
        """A session from an earlier process, continued where it stopped."""
        session = self._sessions.get(session_id)
        if session is None:
            info = await self.call("GET", f"/session/{session_id}")
            # A session belongs to the folder it was made in. Prompting it
            # from another one is accepted by the server and then never
            # answers, so it is treated as gone: a new one is made instead.
            where = str((info or {}).get("directory") or "")
            if where and Path(where).resolve() != Path(self.cwd).resolve():
                raise OpencodeError(
                    f"{self.name}: session {session_id} is in {where}, not {self.cwd}")
            session = Session(self, session_id)
            self._sessions[session_id] = session
        session.on_event = on_event
        return session

    # -- what the stream says --------------------------------------------

    async def _events(self) -> None:
        """One stream for the whole server; events are handed to their session."""
        client = self._client
        if client is None:
            return
        params = {"directory": self.cwd}
        try:
            async with client.stream(
                "GET", "/event", params=params, timeout=httpx.Timeout(None)
            ) as reply:
                reply.raise_for_status()
                async for line in reply.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                    except ValueError:
                        continue  # a stray line, not an event
                    if isinstance(event, dict):
                        self._dispatch(event)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a stream that ends is not a crash
            pass

    def _dispatch(self, event: dict) -> None:
        self._tapped("in", event)
        kind = str(event.get("type") or "")
        props = event.get("properties")
        props = props if isinstance(props, dict) else {}
        session = self._sessions.get(str(props.get("sessionID") or ""))
        if kind == "message.updated":
            info = props.get("info") or {}
            if session is not None and isinstance(info, dict):
                session._roles[str(info.get("id") or "")] = str(
                    info.get("role") or "")
        elif kind == "message.part.updated":
            if session is not None:
                session._part(props.get("part") or {})
        elif kind == "message.part.delta":
            if session is not None:
                session._delta(props)
        elif kind == "session.idle":
            if session is not None:
                session._end_turn()
        elif kind == "session.status":
            status = props.get("status") or {}
            if session is not None and isinstance(status, dict):
                if status.get("type") == "idle":
                    session._end_turn()
        elif kind == "permission.asked":
            asyncio.create_task(self._permitted(session, props))
        elif kind == "question.asked":
            asyncio.create_task(self._questioned(session, props))

    async def _permitted(self, session: Session | None, props: dict) -> None:
        """The agent asks before it acts; every mode answers through here, so
        the floor under them is one place."""
        request_id = str(props.get("id") or "")
        verdict = "reject"
        if session is not None:
            try:
                verdict = await self._decide(session, _as_request(props))
            except Exception:  # noqa: BLE001 — a desk that fails says no
                verdict = "reject"
        reply = "once" if verdict == "allow" else "reject"
        with contextlib.suppress(OpencodeError):
            await self.call(
                "POST", f"/permission/{request_id}/reply", {"reply": reply})

    async def _questioned(self, session: Session | None, props: dict) -> None:
        """The agent asks the person something: the words go through, and
        their answer comes back as the answer."""
        request_id = str(props.get("id") or "")
        questions = props.get("questions")
        questions = questions if isinstance(questions, list) else []
        answers: list[list[str]] = []
        for question in questions:
            text = str((question or {}).get("question") or "")
            said = ""
            if session is not None and self._asked is not None:
                with contextlib.suppress(Exception):
                    said = await self._asked(session, text)
            answers.append([said] if said else [])
        with contextlib.suppress(OpencodeError):
            await self.call(
                "POST", f"/question/{request_id}/reply", {"answers": answers})

    # -- HTTP ------------------------------------------------------------

    async def _http(self, method: str, path: str, body: Any = None,
                    params: dict | None = None) -> Any:
        client = self._client
        if client is None:
            raise OpencodeError(f"{self.name} is not running")
        try:
            reply = await client.request(method, path, json=body, params=params)
        except httpx.HTTPError as exc:
            raise OpencodeError(f"{self.name} {method} {path}: {exc}") from None
        if reply.status_code >= 400:
            raise OpencodeError(
                f"{self.name} {method} {path}: {reply.status_code} "
                f"{reply.text[:200]}")
        if not reply.content:
            return {}
        try:
            return reply.json()
        except ValueError:
            return {}

    async def call(self, method: str, path: str, body: Any = None,
                   params: dict | None = None) -> Any:
        """One call to the server, shown to whoever watches, and never fatal
        for the watcher."""
        self._tapped("out", {"method": method, "path": path})
        return await self._http(method, path, body, params)

    def _tapped(self, direction: str, message: dict) -> None:
        if self._tap is None:
            return
        try:
            self._tap(direction, message)
        except Exception:  # noqa: BLE001 — a watcher's failure is not the agent's
            pass


def _as_request(props: dict) -> dict:
    """A permission event as the tool call a person is owed words for."""
    permission = str(props.get("permission") or "")
    patterns = props.get("patterns")
    patterns = patterns if isinstance(patterns, list) else []
    metadata = props.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    raw: dict[str, Any] = dict(metadata)
    if permission == "bash" and not raw.get("command"):
        raw["command"] = " ".join(str(p) for p in patterns)
    if not any(raw.get(key) for key in ("command", "filePath", "filepath", "path")):
        first = next((str(p) for p in patterns if p), "")
        if first:
            raw["path"] = first
    tool = props.get("tool") or {}
    tool = tool if isinstance(tool, dict) else {}
    return {"toolCall": {
        "toolCallId": str(tool.get("callID") or ""),
        "title": str(props.get("title") or _KINDS.get(permission, permission)),
        "kind": _KINDS.get(permission, permission),
        "rawInput": raw,
        "locations": [{"path": str(p)} for p in patterns],
    }}
