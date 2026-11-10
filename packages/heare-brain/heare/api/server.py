"""The core, opened to the outside: one API for every screen.

The built-in page uses exactly this and nothing else — no private door.
If the page that ships can be built on it, so can the ones a person
makes for themselves.

Eleven groups: messages, events, state, controls, tasks, settings, keys,
memory, mind, screens, features. Each is the core's own surface and holds
no logic of its own.

A key can be written here and can never be read back: a screen a model
drew may save a token, and cannot fetch one.

Bound to this machine and guarded by a token the page is handed when it
is served, so a web page from somewhere else cannot drive the assistant.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import mimetypes
import os
import re
import secrets
import signal
import socket
import subprocess
import time
from importlib import metadata
from pathlib import Path
from urllib.parse import quote
from collections.abc import Callable
from typing import Any, AsyncIterator

from aiohttp import web

from heare import keys
from heare.api import backup
from heare.api.update import UpdateError, Updates
from heare.core import PROTOCOL, Core, Event, Message
from heare import words
from heare.api import auth
from heare.api import pair as codes
from heare.api.grants import Grants
from heare.api.heard import Recordings
from heare.api import skills
from heare.api.machines import Machines
from heare.api.registry import Registry
from heare.api.sessions import MAX_FILE_BYTES, Kept, Sessions
from heare.hand import Gone
from heare.model.providers import PROVIDERS, check_key, list_models

logger = logging.getLogger("heare.api.server")

PAGE = Path(__file__).with_name("page.html")
FONTS = Path(__file__).with_name("fonts")
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,60}")

# How often a quiet stream is written to. A closed page is only noticed when
# a write to it fails, so this is also how long it takes to show it left.
HEARTBEAT_S = 5.0
CONTEXT_TASKS = 10  # how many handed-over tasks the Context page keeps showing

# A body that only shows what is happening may ask for these and nothing
# else. Every one of them can be drawn without the words: a phase, an id, a
# channel. A kind that carries conversation text is not here, so a filtered
# stream cannot be talked into delivering one.
STATUS_KINDS = frozenset({
    "phase", "permission", "permission_closed", "question", "question_closed",
    "said", "failed", "cannot", "halted", "judged", "part", "control", "call",
})

# What survives of each status kind once the stream has asked for status
# only. A kind absent from this table is never in `STATUS_KINDS`; `part` is
# passed through as it is, because there are no words in it.
_STATUS_FIELDS: dict[str, tuple[str, ...]] = {
    "phase": ("phase",),
    "permission": ("id",),
    "permission_closed": ("id", "allowed"),
    "question": ("id",),
    "question_closed": ("id",),
    "said": ("channel", "failed"),
    "failed": ("channel",),
    "judged": ("allowed",),
    "halted": ("tasks",),
    "cannot": (),
    # What a part is told to do: the words to show are the panel's own act.
    "control": ("part", "name", "value"),
    # A skill called on a part: the part answers it by id.
    "call": ("part", "id", "name", "args"),
}


# A window on another machine keeps the pult — the conversation, the page,
# its own body — but not the powers that change this Mac. The local page is
# still the person's. What a remote window may do is named here one route at
# a time; everything else is refused by default, so a route added later is
# local until someone says otherwise. The census in tests/heare/test_api.py
# walks the router and fails when a route is neither here nor explicitly
# local-only.
REMOTE_WINDOW_ALLOWED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"^/api/parts$")),
    ("POST", re.compile(r"^/api/bodies$")),
    ("POST", re.compile(r"^/api/message$")),
    ("POST", re.compile(r"^/api/input$")),
    ("POST", re.compile(r"^/api/input/said_over$")),
    ("GET", re.compile(r"^/api/events$")),
    ("GET", re.compile(r"^/api/state$")),
    ("GET", re.compile(r"^/api/history$")),
    ("GET", re.compile(r"^/api/canvas$")),
    ("GET", re.compile(r"^/api/system$")),
    ("GET", re.compile(r"^/api/tasks$")),
    ("GET", re.compile(r"^/api/controls$")),
    ("GET", re.compile(r"^/api/settings$")),
    ("GET", re.compile(r"^/api/models$")),
    ("GET", re.compile(r"^/api/memory$")),
    ("GET", re.compile(r"^/api/mind$")),
    ("GET", re.compile(r"^/api/mind/journal$")),
    ("GET", re.compile(r"^/api/mind/search$")),
    ("GET", re.compile(r"^/api/heard$")),
    ("GET", re.compile(r"^/api/heard/[^/]+\.wav$")),
    ("GET", re.compile(r"^/api/features$")),
    ("GET", re.compile(r"^/api/intents$")),
    ("POST", re.compile(r"^/api/intents/\d+/now$")),
    ("POST", re.compile(r"^/api/intents/\d+/drop$")),
    ("POST", re.compile(r"^/api/stop$")),
    ("POST", re.compile(r"^/api/tasks/\d+/stop$")),
    ("POST", re.compile(r"^/api/permission/\d+$")),
    ("POST", re.compile(r"^/api/answer/\d+$")),
    ("POST", re.compile(r"^/api/mind/journal/forget$")),
    ("PUT", re.compile(r"^/api/mind/profile$")),
    ("POST", re.compile(r"^/api/heard/[^/]+$")),
)


def WINDOWS_ARE_THE_OWNER() -> bool:
    """A server has no local page. Only for a brain whose page stands behind
    a sign-in of the person's own: then a paired window is the owner."""
    return os.environ.get("HEARE_WINDOWS_ARE_THE_OWNER") == "1"


def _loopback(remote: str | None) -> bool:
    try:
        return ipaddress.ip_address(remote or "").is_loopback
    except ValueError:
        return False


def _remote_window_may(method: str, path: str) -> bool:
    """Whether a window from another machine may use this route.

    A window on this Mac is the person's and is never asked this; it is the
    question the guard puts only to a remote one. A body-less HEAD is the
    GET it stands in for."""
    if method == "HEAD":
        method = "GET"
    return any(method == want and rx.fullmatch(path)
               for want, rx in REMOTE_WINDOW_ALLOWED)


SESSION = "heare_session"
# A backup brought from another Mac: a year of recordings fits well under it.
BACKUP_LIMIT = 2 * 1024 ** 3
# A newer heare is asked about a minute after the start, then once a day.
UPDATE_FIRST_S = 60.0
UPDATE_EVERY_S = 24 * 3600.0
# The machine the brain lives on, as the update card names it: its keeper
# asks with the master token, which no machine key is.
BRAIN_MACHINE = "machine@brain"


class Slow:
    """Ten tries a minute from one address: the code is short, the door is not."""

    def __init__(self, limit: int = 10) -> None:
        self._limit = limit
        self._seen: dict[str, list[float]] = {}

    def allow(self, who: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        seen = [t for t in self._seen.get(who, []) if now - t < 60.0]
        self._seen[who] = seen
        if len(seen) >= self._limit:
            return False
        seen.append(now)
        return True


class Screen:
    """The page as a channel. What is typed there is a message; a reply
    is sent as events, piece by piece, to whoever is watching."""

    name = "screen"
    abilities: frozenset[str] = frozenset()

    def __init__(self, core: Core) -> None:
        self._core = core
        self._speaking = False
        self._stop = False

    @property
    def speaking(self) -> bool:
        return self._speaking

    async def say(self, pieces: AsyncIterator[str]) -> str:
        self._speaking, self._stop = True, False
        try:
            async for piece in pieces:
                if self._stop:
                    return "stopped"
                self._core.state.emit("reply", channel=self.name, text=piece)
            return "done"
        finally:
            self._speaking = False

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def stop(self) -> None:
        self._stop = True


def token(path: Path) -> str:
    """The token, made once and kept where only the person can read it."""
    try:
        found = path.read_text(encoding="utf-8").strip()
        if found:
            return found
    except OSError:
        pass
    made = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(made, encoding="utf-8")
    path.chmod(0o600)
    return made


def running() -> dict:
    """Which heare this is: the version, and the commit when it is a checkout.

    An installed wheel does not know the commit it was built from, and a
    guess is worse than nothing — so the commit is read only from a git
    tree the code itself lives in, never one a virtualenv happens to sit in.
    """
    try:
        version: str | None = metadata.version("heare-brain")
    except metadata.PackageNotFoundError:
        version = None
    here = Path(__file__).resolve()
    commit = None
    if "site-packages" not in here.parts:
        try:
            done = subprocess.run(
                ["git", "-C", str(here.parent), "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=2)
            if done.returncode == 0:
                commit = done.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            commit = None
    return {"version": version, "commit": commit}


class Tally:
    """What this run has added up to: turns, work, money. Counted from
    the core's events, so it is the same account every screen reads."""

    def __init__(self, core: Core) -> None:
        self.started = time.time()
        self.turns = 0
        self.cost_usd = 0.0
        # A call the provider put no price on. Unknown is not free, and
        # is shown as unknown.
        self.unpriced = 0
        self._reader = core.state.subscribe(size=10000)

    def read(self) -> dict:
        while not self._reader.empty():
            event = self._reader.get_nowait()
            if event.kind == "said" and event.data.get("text"):
                self.turns += 1
            elif event.kind == "usage":
                cost = event.data.get("cost_usd")
                if cost is None:
                    self.unpriced += 1
                else:
                    self.cost_usd += float(cost)
        return {"started": self.started, "turns": self.turns,
                "cost_usd": round(self.cost_usd, 6), "unpriced": self.unpriced}


class VoiceOut:
    """A speaking body's side of the door: the reply, piece by piece, and the
    mouth's controls — pause, resume, stop — the body obeys from afar.

    One per channel: the Mac's voice, a phone, a watch. Each hears its own
    replies; one channel shared by two bodies would speak in two rooms."""

    abilities: frozenset[str] = frozenset({"pause"})

    def __init__(self, core: Core, name: str = "voice") -> None:
        self.name = name
        self._core = core
        self._speaking = False
        self._sounding = False
        self._stop = False

    @property
    def speaking(self) -> bool:
        """Streaming or still audible. The body says when the sound ends:
        the text is done long before the room stops hearing it."""
        return self._speaking or self._sounding

    def sounding(self, on: bool) -> None:
        self._sounding = bool(on)

    async def say(self, pieces: AsyncIterator[str]) -> str:
        self._speaking, self._stop = True, False
        try:
            async for piece in pieces:
                if self._stop:
                    return "stopped"
                self._core.state.emit("reply", channel=self.name, text=piece)
            return "done"
        finally:
            self._speaking = False

    def pause(self) -> None:
        self._core.state.emit("voice_paused", channel=self.name)

    def resume(self) -> None:
        self._core.state.emit("voice_resumed", channel=self.name)

    def stop(self) -> None:
        self._stop = True
        self._core.state.emit("voice_stopped", channel=self.name)


class Seen:
    """The parts as the voice sees them: what the registry knows, and a call
    to one of them that waits for its answer — the same call the hands make."""

    def __init__(self, registry: Registry, calls: skills.Calls) -> None:
        self._registry = registry
        self._calls = calls

    def skilled(self) -> dict[str, dict]:
        return self._registry.skilled()

    def parts(self) -> dict[str, dict]:
        return self._registry.parts()

    async def call(self, pid: str, name: str, args: dict) -> dict:
        found = self._registry.skill(pid, name)
        if found is None:
            return {"ok": False, "result": "no such skill"}
        channel, skill = found
        call_id, waiting = self._calls.open(pid)
        self._registry.emit("call", channel=channel, part=pid, id=call_id,
                            name=name, args=args)
        return await self._calls.wait(call_id, waiting, skill.get("within"))



def build(core: Core, settings: Any, vitals: Any = None,
          make_executor: Any = None, *, mind: Any = None,
          features: Any = None, engine: Any = None,
          show_bodies: Callable[[Seen], None] | None = None) -> web.Application:
    """vitals: () -> dict of what the app knows and the core does not —
    which model is answering, and whether it is the one that was chosen.

    mind and features are handed over by the brain. The app is still built
    without them: each surface then says so instead of failing."""
    secret = token(Path(settings.api_token_file))
    part_tokens = Path(settings.part_tokens_file)
    screens = Path(settings.screens_dir)
    recordings = Recordings(Path(settings.heard_dir))
    home = Path(settings.backups_dir).parent
    sessions = Sessions(Path(settings.memory_dir) / "sessions")
    sessions.seed(core.history)
    if not sessions.active:
        sessions.open(sessions.listed()[0]["id"])
    core.history.clear()
    core.hydrate(sessions.for_model(sessions.active))
    kept = Kept(core.memory, sessions) if core.memory is not None else None
    if kept is not None:
        core.memory = kept
    tally = Tally(core)
    # Read once: the version cannot change under a running process.
    build_info = running()
    updates = Updates(settings, build_info["version"], home, core.state.emit)

    PART_CALLS = (
        ("POST", re.compile(r"^/api/parts$")),
        ("POST", re.compile(r"^/api/bodies$")),
        ("POST", re.compile(r"^/api/input$")),
        ("POST", re.compile(r"^/api/input/said_over$")),
        ("GET", re.compile(r"^/api/events$")),
        ("POST", re.compile(r"^/api/keys/ask$")),
        ("POST", re.compile(r"^/api/executor/claim$")),
        ("POST", re.compile(r"^/api/executor/\d+/(step|permit|result|ask)$")),
    )

    PERMISSION = re.compile(r"^/api/permission/\d+$")
    SKILLS = re.compile(r"^/api/skills(/call)?$")
    ANSWER = re.compile(r"^/api/skills/\d+$")

    def part_may(request: web.Request, pid: str) -> bool:
        if request.method == "POST" and PERMISSION.match(request.path):
            # The person's answer, given by a part only with their leave: a
            # trusted part that said it has a yes (the panel's button).
            return registry.approves(pid)
        if SKILLS.match(request.path):
            # What the parts can do, and doing it: for the hands only.
            return registry.works(pid)
        if request.method == "POST" and ANSWER.match(request.path):
            # A part's answer to a call; Calls checks it was this part's.
            return True
        return any(request.method == method and rx.match(request.path)
                   for method, rx in PART_CALLS)

    @web.middleware
    async def guard(request: web.Request, handler):
        if request.path in ("/api/pair", "/health"):
            return await handler(request)
        if not (request.path.startswith("/api/")
                or request.path.startswith("/screens/")):
            return await handler(request)
        given = (request.headers.get("X-Heare-Token")
                 or request.query.get("token")
                 or request.cookies.get(SESSION, "") or "")
        if given and secrets.compare_digest(given, secret):
            return await handler(request)
        pid = auth.owner(part_tokens, given)
        if pid is None:
            raise web.HTTPUnauthorized(text="no token")
        if pid.startswith("window@"):
            # A window on this machine has the person's powers, but is still
            # one window: its knock is its own, so two open pages are two
            # parts, not one. From elsewhere it is the pult only.
            if (not WINDOWS_ARE_THE_OWNER() and not _loopback(request.remote)
                    and not _remote_window_may(request.method, request.path)):
                raise web.HTTPForbidden(
                    text="a window from elsewhere keeps the pult only")
            request["window"] = pid
            return await handler(request)
        if pid.startswith("machine@"):
            if (request.method, request.path) in (
                    ("POST", "/api/parts"), ("POST", "/api/bodies"),
                    ("POST", "/api/machine/mine/report"),
                    ("GET", "/api/update/mine"),
                    ("POST", "/api/update/mine/failed")):
                request["part"] = pid
                return await handler(request)
            raise web.HTTPForbidden(text="a machine only knocks")
        if not part_may(request, pid):
            raise web.HTTPForbidden(text="not for a part")
        request["part"] = pid
        return await handler(request)

    code_path = Path(settings.pair_codes_file)
    pace = Slow()
    minted_at: dict[str, float] = {}

    async def health(request: web.Request) -> web.Response:
        """For the server's watchdog: alive, and nothing more, for anyone."""
        return web.json_response({"ok": True})

    async def pair(request: web.Request) -> web.Response:
        """A machine or a window with a one-time code takes a key of its own."""
        if not pace.allow(request.remote or "?"):
            raise web.HTTPTooManyRequests(text="too many tries")
        data = await body(request)
        kind = str(data.get("kind") or "window")
        if kind not in ("machine", "window"):
            raise web.HTTPBadRequest(text="kind is machine or window")
        if not codes.redeem(code_path, str(data.get("code") or "")):
            raise web.HTTPUnauthorized(text="bad code")
        name = re.sub(r"[^A-Za-z0-9._-]", "", str(data.get("name") or ""))[:40]
        who = name or request.remote or "here"
        # Every tab of one browser calls itself "Chrome". A pid of its own per
        # pairing keeps a second tab from revoking the first one's key, which
        # otherwise makes two open tabs take turns asking for a code.
        pid = f"{kind}@{who}" if kind == "machine" else f"window@{who}-{secrets.token_hex(2)}"
        token = auth.issue(part_tokens, pid)
        if kind == "machine":
            return web.json_response({"pid": pid, "token": token})
        answer = web.json_response({"pid": pid})
        secure = (request.scheme == "https"
                  or request.headers.get("X-Forwarded-Proto") == "https")
        answer.set_cookie(SESSION, token, httponly=True, samesite="Strict",
                          secure=secure, max_age=30 * 24 * 3600, path="/")
        return answer

    async def pair_code(request: web.Request) -> web.Response:
        """A paired page mints a code for a browser that is not paired yet."""
        who = request.remote or "?"
        now = time.time()
        if now - minted_at.get(who, 0.0) < 2.0:
            raise web.HTTPTooManyRequests(text="too fast")
        minted_at[who] = now
        return web.json_response({
            "code": codes.make(code_path),
            "ttl": int(codes.TTL_S),
        })

    async def body(request: web.Request) -> dict:
        try:
            data = await request.json()
        except (ValueError, json.JSONDecodeError):
            raise web.HTTPBadRequest(text="not JSON") from None
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(text="an object is expected")
        return data

    def handed(html: str) -> web.Response:
        """A page as it is. A key is never written into it: the page pairs."""
        return web.Response(text=html, content_type="text/html")

    # -- messages ---------------------------------------------------------

    async def message(request: web.Request) -> web.Response:
        data = await body(request)
        text = str(data.get("text", "")).strip()
        ids = data.get("files") if isinstance(data.get("files"), list) else []
        attached, metas = sessions.block(sessions.active, ids)
        if not text and not metas:
            raise web.HTTPBadRequest(text="nothing was said")
        if kept is not None:
            kept.files = metas
        core.hear(Message(text=(text or "(see attached)") + attached, channel="screen"))
        return web.json_response({"ok": True})

    # -- sessions: the talk divided -------------------------------------------

    def session_of(request: web.Request) -> str:
        session = request.match_info["id"]
        if not sessions.has(session):
            raise web.HTTPNotFound(text="no such session")
        return session

    def idle() -> None:
        # A turn under way belongs to the session it began in.
        if core.state.phase != "idle":
            raise web.HTTPConflict(text="a turn is under way")

    def went_to(session: str) -> web.Response:
        core.history.clear()
        if session:
            core.hydrate(sessions.for_model(session))
        core.state.emit("sessions", active=session)
        return web.json_response({"active": session})

    async def sessions_list(request: web.Request) -> web.Response:
        return web.json_response({"active": sessions.active, "items": sessions.listed()})

    async def session_new(request: web.Request) -> web.Response:
        idle()
        return went_to(sessions.new())

    async def session_open(request: web.Request) -> web.Response:
        session = session_of(request)
        idle()
        sessions.open(session)
        return went_to(session)

    async def session_rename(request: web.Request) -> web.Response:
        session = session_of(request)
        sessions.rename(session, str((await body(request)).get("title", "")).strip())
        core.state.emit("sessions", active=sessions.active)
        return web.json_response({"ok": True})

    async def session_delete(request: web.Request) -> web.Response:
        session = session_of(request)
        idle()
        sessions.delete(session)
        if not sessions.active:
            sessions.new()
        return went_to(sessions.active)

    async def session_turns(request: web.Request) -> web.Response:
        return web.json_response(sessions.read(session_of(request)))

    async def session_upload(request: web.Request) -> web.Response:
        session = session_of(request)
        data = await request.read()
        if not data:
            raise web.HTTPBadRequest(text="an empty file")
        return web.json_response(sessions.store(session, request.query.get("name", "file"), data))

    async def session_file(request: web.Request) -> web.StreamResponse:
        found = sessions.stored(session_of(request), request.match_info["file"])
        if found is None:
            raise web.HTTPNotFound(text="no such file")
        meta, path = found
        shown = "inline" if meta["kind"] == "image" else "attachment"
        return web.FileResponse(path, headers={
            "Content-Disposition": f"{shown}; filename*=UTF-8''{quote(meta['name'])}",
            "Content-Type": mimetypes.guess_type(meta["name"])[0] if shown == "inline"
            else "application/octet-stream",
            "X-Content-Type-Options": "nosniff"})

    async def follow_tasks(app: web.Application) -> None:
        """Work that ends in another session is written to the one that asked."""
        origin: dict[int, str] = {}
        reader = core.state.subscribe()

        async def follow() -> None:
            while True:
                event = await reader.get()
                if event.kind != "task":
                    continue
                task = event.data["id"]
                began = origin.setdefault(task, sessions.active)
                news = event.data.get("news")
                if news and began != sessions.active:
                    sessions.add(began, "assistant", news, unread=True)
                    core.state.emit("sessions", active=sessions.active)

        follower = asyncio.create_task(follow(), name="sessions-tasks")

        async def stop(_: web.Application) -> None:
            follower.cancel()

        app.on_cleanup.append(stop)

    def on_executor(pid: str, channel: str, about: str, machine: str) -> None:
        """One executor per pid: a re-knock updates its card, not the
        object that holds the jobs already handed to it."""
        found = core.executors.get(pid)
        if found is not None:
            try:
                found.about = about
            except (AttributeError, TypeError):
                pass
            return
        core.add_executor(make_executor(pid, channel, about, machine))

    def _present(pid: str, on: bool) -> None:
        """The door says whether a part's stream is here or away."""
        found = core.executors.get(pid)
        if found is not None and hasattr(found, "set_present"):
            found.set_present(on)

    def _drop_executor(pid: str) -> None:
        """One way out for a hand: its queued work fails, then it is gone."""
        found = core.executors.pop(pid, None)
        if found is not None and hasattr(found, "close"):
            found.close()

    machines = Machines(Path(settings.parts_dir).parent / "machines.json")
    registry = Registry(
        Path(settings.parts_file), core.state.emit,
        on_executor if make_executor is not None else None,
        on_no_executor=_drop_executor,
        on_present=_present)
    registry.load(Path(settings.parts_dir))

    def machines_view() -> list[dict]:
        """The machines as the page sees them: reports, parts, and keys."""
        shown = machines.view(
            registry.known, time.time(),
            tuple(auth.pids(part_tokens)))
        for machine in shown:
            machine["keyed"] = auth.known(part_tokens, machine["pid"])
            machine["brain"] = machine["pid"] == f"machine@{settings.host}"
        return shown

    bodies = registry.known
    trust = skills.Trust(Path(settings.parts_file).parent / "part_skills.json")
    calls = skills.Calls()
    seen = Seen(registry, calls)
    if show_bodies is not None:
        # The voice reads the same truth as the hands: what is let in and here.
        show_bodies(seen)

    def own_channel(request: web.Request, channel: str) -> None:
        """A part may speak for its own channel and no other."""
        pid = request.get("part")
        if pid is not None and channel and channel != registry.channel_of(pid):
            raise web.HTTPForbidden(text="not your channel")

    async def claim(request: web.Request) -> web.Response:
        """A body's hands ask for work; the brain hands one out, or nothing."""
        found = core.executors.get(str(request.get("part") or ""))
        if not hasattr(found, "claim"):
            raise web.HTTPNotFound(text="no such body")
        task = await found.claim()
        return web.json_response(task or {})

    async def step(request: web.Request) -> web.Response:
        data = await body(request)
        found = _executor_for(request)
        return web.json_response({"stopped": found.step(
            int(request.match_info["id"]), str(data.get("text", "")))})

    async def executor_permit(request: web.Request) -> web.Response:
        data = await body(request)
        found = _executor_for(request)
        job = int(request.match_info["id"])
        allow, stopped = await found.permit(
            job, str(data.get("what", "")), str(data.get("kind", "change")),
            str(data.get("detail", ""))[:4000],
        )
        by, why = found.verdict(job) if hasattr(found, "verdict") else ("person", "")
        return web.json_response({"allow": bool(allow), "stopped": bool(stopped),
                                  "by": by, "why": why})

    async def executor_ask(request: web.Request) -> web.Response:
        """The agent's question mid-work: the person's answer comes back."""
        data = await body(request)
        found = _executor_for(request)
        try:
            answer = await found.ask(int(request.match_info["id"]),
                                     str(data.get("question", "")))
        except Gone:
            raise web.HTTPNotFound(text="no such job") from None
        return web.json_response({"answer": answer})

    async def result(request: web.Request) -> web.Response:
        data = await body(request)
        found = _executor_for(request)
        job = int(request.match_info["id"])
        if not found.result(
            job, str(data.get("status", "done")), str(data.get("result", "")),
        ):
            logger.warning("a result came for unknown job %s from %s", job,
                           request["part"])
            raise web.HTTPNotFound(text="no such job")
        return web.json_response({"ok": True})

    def _executor_for(request: web.Request):
        found = core.executors.get(str(request.get("part") or ""))
        if not hasattr(found, "claim"):
            raise web.HTTPNotFound(text="no such body")
        return found

    def named() -> dict:
        """The assistant's name, for a part that shows what it says: a part
        may not read the state, so the knock's answer carries it."""
        try:
            name = (vitals() or {}).get("name") if vitals is not None else None
        except Exception:  # noqa: BLE001 — a name is a nicety, never a failed knock
            name = None
        # Where the brain is in time: a part with a clock and no zone of its
        # own (the panel) shows the brain's local time, summer time included.
        offset = {"utc_offset": int(time.localtime().tm_gmtoff or 0)}
        offset["language"] = str(settings.language)
        return {"name": name, **offset} if name else offset

    def granted(request: web.Request, pid: str) -> dict:
        """A key of its own for a part that knocked with the master or a machine key.

        Never for a window: it already holds its key in a cookie, and a new
        one would replace it and send the page back to pairing."""
        who = request.get("part") or request.get("window")
        if who is not None and not who.startswith("machine@"):
            return {}
        return {"token": auth.issue(part_tokens, pid)}

    def machine_may_claim(who: str, pid: str, channel: str, forms: list) -> bool:
        """A machine gets only the pids it claimed and never a part someone
        already has. Hands are per pid: many machines may each bring their
        own of one kind.

        A manifest is a folder the person put there by hand; it is not a
        claim, so an away entry whose host is this machine may still be
        claimed. An approved or pending local part may not."""
        name = who.split("@", 1)[1]
        if pid.split("@", 1)[-1] != name:
            return False
        entry = registry.known.get(pid)
        if entry is not None:
            claimed = entry.get("machine")
            if claimed is not None and claimed != who:
                return False
            if claimed is None and entry.get("state") != "away":
                return False
        if "executor" in forms:
            return True
        owner = registry.one_on(channel)
        if owner is not None:
            owner_pid, owner_entry = owner
            if owner_pid != pid and (owner_entry.get("state") == "approved"
                                     or owner_entry.get("machine") != who):
                return False
        if registry.executor_on(channel):
            holder = registry.known.get(pid)
            if holder is None or holder.get("channel") != channel:
                return False
        return True

    def gives_voice(channel: str, forms: list, window: bool) -> None:
        """A part that speaks gets a mouth of its own on the brain: without
        one its replies fell to the first output there was (the page). A
        window is the page itself and has one."""
        if (not window and "output" in forms and channel not in core.outputs
                and not registry.executor_on(channel)):
            core.add_output(VoiceOut(core, channel))

    async def machine_report(request: web.Request) -> web.Response:
        """A keeper says what its machine has; the answer is what to run.

        A machine's own key says which machine it is. The brain's own keeper
        has the master key and no machine name of its own, so it says the
        name in the body — it is the owner on the loopback, and a window is
        not a machine.
        """
        data = await body(request)
        given = str(request.get("part") or "")
        if given and not given.startswith("machine@"):
            raise web.HTTPForbidden(text="not a machine")
        if not given:
            if request.get("window"):
                raise web.HTTPForbidden(text="a window is not a machine")
            name = re.sub(r"[^A-Za-z0-9._-]", "", str(data.get("name") or ""))[:40]
            if not name:
                raise web.HTTPBadRequest(text="a machine needs a name")
            given = f"machine@{name}"
        return web.json_response(machines.report(given, data, time.time()))

    async def machines_list(request: web.Request) -> web.Response:
        return web.json_response({"machines": machines_view()})

    async def machine_part(request: web.Request) -> web.Response:
        """The person's switch: what this machine should run from now on."""
        name = request.match_info["name"]
        part = request.match_info["part"]
        data = await body(request)
        on = data.get("on")
        if not isinstance(on, bool):
            raise web.HTTPBadRequest(text="on is true or false")
        if not machines.desire(f"machine@{name}", part, on):
            raise web.HTTPBadRequest(text="not a part name")
        return web.json_response({"machines": machines_view()})

    async def machine_restart(request: web.Request) -> web.Response:
        """One part starts again: a word to the keeper, not a new state."""
        name = request.match_info["name"]
        part = request.match_info["part"]
        if not machines.restart(f"machine@{name}", part):
            raise web.HTTPBadRequest(text="not a part name")
        return web.json_response({"ok": True})

    async def machine_rename(request: web.Request) -> web.Response:
        """A name for the page; the key stays, so no part is re-keyed."""
        data = await body(request)
        name = request.match_info["name"]
        if not machines.rename(f"machine@{name}", str(data.get("alias") or "")):
            raise web.HTTPNotFound(text="no such machine")
        return web.json_response({"machines": machines_view()})

    async def machine_forget(request: web.Request) -> web.Response:
        """Revoke the machine: its key, its parts' keys, and the card.

        The keeper on that machine is not reached — the brain cannot knock.
        It learns when its next report is refused and parks the parts.
        """
        pid = f"machine@{request.match_info['name']}"
        for part_pid in [one for one, entry in registry.known.items()
                         if entry.get("machine") == pid]:
            registry.forget(part_pid)
            auth.revoke(part_tokens, part_pid)
            lent.forget(part_pid)
            _drop_executor(part_pid)
        auth.revoke(part_tokens, pid)
        machines.forget(pid)
        return web.json_response({"machines": machines_view()})

    async def register(request: web.Request) -> web.Response:
        """A part knocks; the person decides whether to trust it.

        The request waits for that answer: a part not trusted is not used, and
        a part nobody answered leaves rather than lurk."""
        data = await body(request)
        channel = str(data.get("channel") or "").strip()
        if not channel:
            raise web.HTTPBadRequest(text="a part needs a channel")
        who = request.get("part") or request.get("window")
        window = who is not None and who.startswith("window@")
        if not window:
            version = data.get("v")
            if version is None:
                return web.json_response({"approved": False, "why": (
                    "the part speaks no protocol version")})
            if version != PROTOCOL:
                # Said, not half understood: a part of another wire leaves with
                # the reason instead of talking past the brain.
                return web.json_response({"approved": False, "why": (
                    f"the brain speaks protocol v{PROTOCOL}, the part v{version}")})
        # A browser cannot name its machine; a window is called what it was
        # paired as, never after the brain's host.
        host = str(data.get("host") or (who.split("@", 1)[1] if window
                                        else socket.gethostname()))
        pid = who if window else str(data.get("key") or f"{channel}@{host}")
        machine = who if str(who or "").startswith("machine@") else None
        # A part back on its own key after the brain restarted is still its
        # machine's: forgetting that made the machine's next knock look like
        # taking a name (2 November: every part fell at once on an update).
        if machine is None and who == pid and not window:
            mine = f"machine@{pid.split('@', 1)[-1]}"
            machine = mine if auth.known(part_tokens, mine) else None
        if who is not None and who != pid and not machine:
            raise web.HTTPForbidden(text="not your name")
        forms = list(data.get("forms") or [])
        if machine is not None and not machine_may_claim(machine, pid, channel, forms):
            raise web.HTTPForbidden(text="not your name")
        hears = "all" if data.get("hears") == "all" else "own"
        # The part's own version: shown on its card, never a reason to trust.
        part_version = str(data["version"]) if data.get("version") else None
        try:
            started = float(data["started"]) if data.get("started") else None
        except (TypeError, ValueError):
            started = None
        about = str(data.get("about") or "")
        approves = data.get("approves") is True
        # What the part can do, in its own words.
        offered = skills.checked(data.get("skills"))
        named_as = str(data.get("name") or "")[:32]
        shape = {"channel": channel, "forms": sorted(str(f) for f in forms),
                 "hears": hears, "approves": approves}
        ui = list(data.get("ui") or []) or list(
            (bodies.get(pid) or {}).get("ui") or [])
        # A part whose skills or shape are not those it was let in with is
        # asked about again: a new switch or a wider ear is a new trust.
        if ((pid in registry.trusted() and trust.same(pid, offered, shape))
                or str(who or "").startswith(("window@", "machine@"))):
            if machine is not None:
                # A paired machine vouches for its parts once: the part comes
                # back with a key of its own, and after the brain restarts it
                # must not wait for a yes it was never asked for (24 October:
                # the sources stood pending after every brain restart).
                registry.remember(pid)
                trust.keep(pid, offered, shape)
            gives_voice(channel, forms, window)
            registry.attach(pid, channel, forms, about, host, "approved", ui, hears,
                            version=part_version, started=started,
                            approves=approves, skills=offered, name=named_as,
                            machine=machine)
            return web.json_response({"approved": True, "pid": pid, **named(),
                                      **granted(request, pid)})
        gives_voice(channel, forms, window)
        registry.attach(pid, channel, forms, about, host, "pending", ui, hears,
                        version=part_version, started=started,
                            approves=approves, skills=offered, name=named_as,
                            machine=machine)
        # A part waiting for a new yes does not keep the key from the old one.
        auth.revoke(part_tokens, pid)
        decided = await registry.answer(pid)
        if decided == "expired":
            auth.revoke(part_tokens, pid)
            return web.json_response({"approved": False, "why": "nobody answered"})
        if decided != "approved":
            auth.revoke(part_tokens, pid)
            return web.json_response({"approved": False, "why": decided})
        trust.keep(pid, offered, shape)
        registry.attach(pid, channel, forms, about, host, "approved", ui, hears,
                        version=part_version, started=started,
                            approves=approves, skills=offered, name=named_as,
                            machine=machine)
        # Its old key went with the old yes; the new yes comes with a new one.
        return web.json_response({"approved": True, "pid": pid, **named(),
                                  "token": auth.issue(part_tokens, pid)})

    async def approve(request: web.Request) -> web.Response:
        return _decide(request, "approved")

    async def reject(request: web.Request) -> web.Response:
        return _decide(request, "rejected")

    lent = Grants(Path(settings.part_tokens_file).with_name("key_grants.json"))

    async def key_ask(request: web.Request) -> web.Response:
        """A part asks for a key: for the code that said yes, or it waits."""
        pid = request.get("part")
        if pid is None or pid.startswith("machine@"):
            raise web.HTTPForbidden(text="only a part borrows a key")
        data = await body(request)
        name = str(data.get("key") or "")
        if name not in keys.NAMES:
            raise web.HTTPBadRequest(text="no such key")
        code = str(data.get("fingerprint") or "")[:64]
        if not lent.allowed(pid, name, code):
            lent.ask(pid, name, code, str(data.get("version") or "")[:32])
            return web.json_response({"waiting": True})
        value = keys.get(name)
        logger.info("key %s lent to %s", name, pid)
        return web.json_response({"value": value} if value else {"missing": True})

    async def key_asked(request: web.Request) -> web.Response:
        return web.json_response(sorted(
            lent.waiting.values(), key=lambda asked: (asked["pid"], asked["key"])))

    async def key_answer(request: web.Request) -> web.Response:
        """The person's word; a host answers all that machine has asked."""
        data = await body(request)
        allow = data.get("allow") is True
        host = str(data.get("host") or "")
        chosen = [k for k in lent.waiting if host and k[0].split("@", 1)[-1] == host]
        if not host:
            chosen = [(str(data.get("pid") or ""), str(data.get("key") or ""))]
        told = [lent.answer(pid, key, allow) for pid, key in chosen]
        for (pid, key), done in zip(chosen, told):
            if done:
                logger.info("key %s for %s: %s", key, pid, "yes" if allow else "no")
        return web.json_response({"answered": sum(told)})

    async def forget(request: web.Request) -> web.Response:
        """The person takes a part out of the registry and out of trust.

        The brain cannot kill a process it does not own, and does not
        pretend to: the part keeps running, but is no longer known — the
        next knock is a question again."""
        pid = request.match_info["id"]
        entry = registry.forget(pid)
        if entry is None:
            raise web.HTTPNotFound(text="no such part")
        auth.revoke(part_tokens, pid)
        lent.forget(pid)
        _drop_executor(pid)
        return web.json_response({"ok": True})

    def _decide(request: web.Request, state: str) -> web.Response:
        pid = request.match_info["id"]
        if not registry.decide(pid, state):
            raise web.HTTPNotFound(text="no such part is waiting")
        if state != "approved":
            auth.revoke(part_tokens, pid)
        return web.json_response({"ok": True})

    async def input_event(request: web.Request) -> web.Response:
        """A body's side: what it heard, and what it did with it."""
        data = await body(request)
        kind = str(data.get("kind", ""))
        channel = str(data.get("channel") or "text")
        own_channel(request, channel)
        if kind == "started":
            core.port.started(channel)
        elif kind == "nothing":
            core.port.nothing(channel)
        elif kind == "overheard":
            core.port.overheard(channel, str(data.get("text", "")))
        elif kind == "note":
            core.port.note(str(data.get("channel") or ""),
                           str(data.get("key", "")), data.get("value"))
        elif kind == "state":
            values = data.get("values")
            if not isinstance(values, dict):
                raise web.HTTPBadRequest(text="values must be an object")
            # A part reports for itself: several pages share the channel
            # "web", and two hands of one channel share it too, so the part
            # that reported updates its own card.
            entry = (bodies.get(request.get("window") or "")
                     or bodies.get(str(request.get("part") or "")))
            if entry is None:
                raise web.HTTPNotFound(text="no such part")
            entry["values"].update(values)
            if "speaking" in values:
                out = core.outputs.get(channel)
                if isinstance(out, VoiceOut):
                    out.sounding(bool(values["speaking"]))
                    if not values["speaking"]:
                        # Without this the phase stayed «speaking» until the
                        # next event — a minute and more (25 October, seen
                        # as the menu bar's face that never stopped talking).
                        core.port.sounded()
            core.state.emit("part", channel=entry["channel"],
                            forms=entry["forms"], about=entry["about"],
                            host=entry["host"], state=entry["state"])
        elif kind == "heard":
            text = str(data.get("text", "")).strip()
            if not text:
                raise web.HTTPBadRequest(text="nothing was said")
            pid = str(request.get("part") or "")
            entry = bodies.get(pid) or {}
            origin = "" if pid.startswith("window@") else str(entry.get("host") or "")
            core.port.heard(Message(
                text=text, channel=channel,
                addressed=bool(data.get("addressed", True)),
                speech_ms=data.get("speech_ms"),
                origin=origin,
            ))
        else:
            raise web.HTTPBadRequest(text="unknown input event")
        return web.json_response({"ok": True})

    async def said_over(request: web.Request) -> web.Response:
        """A voice over the reply: did it mean a turn, or was it a nod?"""
        data = await body(request)
        channel = str(data.get("channel") or "text")
        own_channel(request, channel)
        turn = core.port.said_over(
            channel,
            str(data.get("text", "")),
            float(data.get("speech_ms") or 0.0),
            bool(data.get("long", False)),
        )
        return web.json_response({"turn": bool(turn)})

    # -- events and state -------------------------------------------------

    async def events(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={
            "Content-Type": "text/event-stream", "Cache-Control": "no-cache",
        })
        await response.prepare(request)
        reader = core.state.subscribe()
        who = request.get("part") or request.get("window")
        if who is not None:
            registry.listens(who)
        # A window is the person and hears all; a part, what is its own.
        listener = request.get("part")
        # `kinds` narrows a stream; it never adds a kind the caller could not
        # already hear, so it is applied after `hears`, never instead of it.
        asked = _requested(request)

        def for_stream(event: Event) -> dict | None:
            if not registry.hears(listener, event.data):
                return None
            if asked is None:
                return event.view()
            if event.kind not in asked:
                return None
            return _status_view(event)

        try:
            if asked is not None:
                # What the present is, before anything changes: a panel that
                # has just connected has seen no phase event and no ids.
                now = time.time()
                present = [Event("phase", {"phase": core.state.phase}, now)]
                if core.state.permission is not None:
                    present.append(
                        Event("permission", core.state.permission.view(), now))
                if core.state.question is not None:
                    present.append(Event("question", core.state.question.view(), now))
                for event in present:
                    seen = for_stream(event)
                    if seen is not None:
                        await response.write(_sse(seen))
            # The past only when asked for: a page that also loads the
            # history would otherwise show the last exchange twice.
            replay = max(0, min(int(request.query.get("replay", "0")), 200))
            for past in list(core.state.journal)[-replay:] if replay else ():
                seen = for_stream(past)
                if seen is not None:
                    await response.write(_sse(seen))
            while True:
                try:
                    event = await asyncio.wait_for(reader.get(), HEARTBEAT_S)
                    seen = for_stream(event)
                    if seen is not None:
                        await response.write(_sse(seen))
                except TimeoutError:
                    await response.write(b": still here\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            core.state.unsubscribe(reader)
            if who is not None:
                registry.left(who)
        return response

    async def state(request: web.Request) -> web.Response:
        about: dict = {}
        if vitals is not None:
            try:
                about = vitals()
            except Exception as exc:  # noqa: BLE001 — a vital that fails is shown, not hidden
                about = {"error": str(exc)}
        return web.json_response({
            **core.state.snapshot(), "controls": core.controls(),
            "model": about, "session": tally.read(), "bodies": bodies,
            "running": build_info, "update": updates.state,
        })

    async def history(request: web.Request) -> web.Response:
        """The conversation before this page was opened."""
        recent = getattr(core.memory, "recent", None)
        if recent is None:
            return web.json_response([])
        count = max(1, min(int(request.query.get("n", "20")), 100))
        return web.json_response(await recent(count))

    # -- controls ---------------------------------------------------------

    async def controls(request: web.Request) -> web.Response:
        return web.json_response(core.controls())

    async def control(request: web.Request) -> web.Response:
        data = await body(request)
        part = str(data.get("part", ""))
        name = str(data.get("name", ""))
        value = data.get("value")
        target = bodies.get(part)
        if target is not None:
            names = {str(c.get("name", "")) for c in target.get("ui") or []}
            if target.get("state") != "approved" or name not in names:
                raise web.HTTPNotFound(text="no such control")
            # Addressed to the part as well as the channel: pages share the
            # channel "web", and a switch on one page's card is that page's.
            core.state.emit("control", channel=target["channel"], name=name,
                            value=value, part=part)
            return web.json_response({"ok": True})
        try:
            now = core.control(part, name, value)
        except KeyError:
            raise web.HTTPNotFound(text="no such control") from None
        except (ValueError, TypeError) as exc:
            raise web.HTTPBadRequest(text=str(exc)) from None
        return web.json_response(now)

    # -- skills: what the hands may do with the parts -----------------------

    async def system_prompt(request: web.Request) -> web.Response:
        """What the model reads every turn, for a person to look at."""
        return web.Response(text=await core.system())

    async def context(request: web.Request) -> web.Response:
        """What really went out: the last turn's messages, and each task's brief."""
        recent = sorted(core.state.tasks.values(), key=lambda t: t.id)[-CONTEXT_TASKS:]
        return web.json_response({
            "voice": core.last_context,
            "turns": list(core.contexts),
            "limit": int(settings.context_tokens),
            "tasks": [{"id": t.id, "executor": t.executor, "status": t.status,
                       "started": t.started, "request": t.request, "brief": t.brief}
                      for t in reversed(recent)],
        })

    async def skills_list(request: web.Request) -> web.Response:
        return web.json_response(registry.skilled())

    async def skill_call(request: web.Request) -> web.Response:
        """One skill of one part, called; waits for the part's answer."""
        data = await body(request)
        pid, name = str(data.get("part", "")), str(data.get("name", ""))
        args = data.get("args") if isinstance(data.get("args"), dict) else {}
        if registry.skill(pid, name) is None:
            raise web.HTTPNotFound(text="no such skill")
        return web.json_response(await seen.call(pid, name, args))

    async def skill_answer(request: web.Request) -> web.Response:
        data = await body(request)
        pid = request.get("part") or str(data.get("part", ""))
        if not calls.answer(int(request.match_info["id"]), pid,
                            data.get("ok") is not False, data.get("result")):
            raise web.HTTPNotFound(text="no such call")
        return web.json_response({"ok": True})

    async def stop(request: web.Request) -> web.Response:
        return web.json_response({"tasks_stopped": await core.halt()})

    async def canvas(request: web.Request) -> web.Response:
        """What is drawn now: the widgets, and the canvas's own stylesheet."""
        drawn = core.outputs.get("canvas")
        return web.json_response(
            drawn.view() if drawn is not None else {"blocks": [], "css": ""}
        )

    async def canvas_remove(request: web.Request) -> web.Response:
        """The person closing a widget by hand."""
        drawn = core.outputs.get("canvas")
        name = str((await body(request)).get("id", ""))
        return web.json_response({"removed": bool(drawn and drawn.remove(name))})

    async def canvas_put(request: web.Request) -> web.Response:
        """The person redrawing a widget by hand. The same block the model
        drew, put back in its place, so the drawing stays one drawing."""
        drawn = core.outputs.get("canvas")
        if drawn is None:
            raise web.HTTPConflict(text="nothing is drawn here")
        data = await body(request)
        try:
            drawn.put(str(data.get("id", "")), str(data.get("html", "")))
        except ValueError:
            raise web.HTTPBadRequest(
                text="a block's name is lowercase latin letters, digits, dashes"
            ) from None
        return web.json_response(drawn.view())

    async def machine(request: web.Request) -> web.Response:
        from heare.api import system

        return web.json_response(await asyncio.to_thread(system.read))

    # -- tasks ------------------------------------------------------------

    async def tasks(request: web.Request) -> web.Response:
        return web.json_response(core.state.snapshot()["tasks"])

    async def start_task(request: web.Request) -> web.Response:
        request_text = str((await body(request)).get("request", "")).strip()
        if not request_text:
            raise web.HTTPBadRequest(text="nothing to do")
        if not core.executors:
            # Said plainly, and written down: this is the list of what to build.
            core.state.emit("cannot", what=request_text, why="no executor connected")
            raise web.HTTPConflict(text="no executor is connected")
        return web.json_response(core.delegate(request_text).view())

    async def stop_task(request: web.Request) -> web.Response:
        return web.json_response({"stopped": core.stop_task(int(request.match_info["id"]))})

    async def answer(request: web.Request) -> web.Response:
        """The person's words for an executor's open question."""
        data = await body(request)
        ok = core.answer(int(request.match_info["id"]), str(data.get("text", "")))
        return web.json_response({"ok": bool(ok)})

    async def permit(request: web.Request) -> web.Response:
        allow = bool((await body(request)).get("allow"))
        if request.get("part") and not allow:
            # A button can say yes; no is the person's, by voice or page.
            raise web.HTTPForbidden(text="a part only says yes")
        return web.json_response(
            {"answered": core.permit(int(request.match_info["id"]), allow)}
        )

    # -- settings and keys ------------------------------------------------

    async def get_settings(request: web.Request) -> web.Response:
        return web.json_response(settings.view())

    async def set_settings(request: web.Request) -> web.Response:
        try:
            for name, value in (await body(request)).items():
                settings.set(name, value)
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from None
        return web.json_response(settings.view())

    async def models(request: web.Request) -> web.Response:
        """The models a provider offers, live when there is a key.

        The live list is a convenience, not a dependency: no key, a bad
        key or a dead endpoint all fall back to the whitelist. The key
        itself never leaves this process.
        """
        name = request.query.get("provider", "").strip().lower()
        config = PROVIDERS.get(name)
        if config is None:
            raise web.HTTPBadRequest(text="unknown provider")
        key = await asyncio.to_thread(keys.get, config.api_key_env)
        source = "whitelist"
        found = list(config.model_whitelist)
        if key:
            try:
                found = await list_models(config, key)
                source = "live"
            except Exception:  # noqa: BLE001 — the whitelist is the fallback
                found = list(config.model_whitelist)
        return web.json_response({
            "provider": config.key,
            "models": found,
            "source": source,
            "default": config.default_model,
        })

    async def get_keys(request: web.Request) -> web.Response:
        """Where each key comes from. Never what it is."""
        return web.json_response(await asyncio.to_thread(keys.view))

    async def set_key(request: web.Request) -> web.Response:
        data = await body(request)
        name, value = str(data.get("name", "")), str(data.get("value", "")).strip()
        if name not in keys.NAMES or not value:
            raise web.HTTPBadRequest(text="not a key this assistant uses")
        if not value.isascii() or any(c.isspace() for c in value):
            raise web.HTTPBadRequest(text="this does not look like a key: latin letters and digits only")
        ok, why = await check_key(name, value)
        if not ok:
            raise web.HTTPBadRequest(text=why)
        await asyncio.to_thread(keys.store, name, value)
        core.state.emit("key", name=name)
        return web.json_response({"saved": name, "why": why})

    async def adopt_key(request: web.Request) -> web.Response:
        """Copy a key the environment already holds into the store."""
        name = str((await body(request)).get("name", ""))
        if name not in keys.NAMES:
            raise web.HTTPBadRequest(text="not a key this assistant uses")
        if not await asyncio.to_thread(keys.adopt, name):
            raise web.HTTPBadRequest(text="no such key in the environment")
        core.state.emit("key", name=name)
        return web.json_response({"saved": name})

    # -- memory -----------------------------------------------------------

    async def memory(request: web.Request) -> web.Response:
        query = request.query.get("q", "").strip()
        if core.memory is None or not query:
            raise web.HTTPBadRequest(text="nothing to look for")
        found = await core.memory.recall(query, request.query.get("when", ""))
        return web.json_response({"said": found})

    # -- features ---------------------------------------------------------

    async def list_features(request: web.Request) -> web.Response:
        if features is None:
            return web.json_response({"groups": [], "features": []})
        return web.json_response(await asyncio.to_thread(features.list))

    async def act_feature(request: web.Request) -> web.Response:
        if features is None or not features.has(request.match_info["id"]):
            raise web.HTTPNotFound(text="no such feature")
        data = await body(request)
        try:
            answer = await features.act(
                request.match_info["id"], data.get("name"), data.get("value"))
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from None
        return web.json_response(answer)

    # -- mind -------------------------------------------------------------

    def hand() -> Any:
        if mind is None:
            raise web.HTTPServiceUnavailable(text="memory is not open here")
        return mind

    async def mind_snapshot(request: web.Request) -> web.Response:
        return web.json_response(await asyncio.to_thread(hand().snapshot))

    # -- other tools' memory: read here, sent to the model only on a press ------

    async def sources_list(request: web.Request) -> web.Response:
        return web.json_response(await asyncio.to_thread(hand().sources))

    async def source_import(request: web.Request) -> web.Response:
        name = request.match_info["name"]
        known = await asyncio.to_thread(hand().sources)
        if not any(s["name"] == name and s["found"] for s in known):
            raise web.HTTPNotFound(text="no such source")
        if hand().importing(name):
            raise web.HTTPConflict(text="already reading it")
        kinds = (await body(request)).get("kinds")

        def told(done: int, total: int) -> None:
            core.state.emit("sources", name=name, done=done, total=total)

        def ended(report: Any) -> None:
            core.state.emit("sources", name=name, finished=True, added=report.added,
                            read=report.read, error=report.error)
            core.state.emit("mind", what="fact", action="imported")

        hand().start_import(name, set(kinds) if isinstance(kinds, list) else None,
                            core.model, told, ended)
        told(0, 0)
        return web.json_response({"started": True}, status=202)

    async def source_undo(request: web.Request) -> web.Response:
        gone = await asyncio.to_thread(hand().undo_source, request.match_info["name"])
        core.state.emit("mind", what="fact", action="undone")
        return web.json_response({"gone": gone})

    async def confirm_fact(request: web.Request) -> web.Response:
        fact_id = int(request.match_info["id"])
        if not await asyncio.to_thread(hand().confirm, fact_id):
            raise web.HTTPNotFound(text="no such fact")
        core.state.emit("mind", what="fact", action="confirmed", id=fact_id)
        return web.json_response({"ok": True})

    async def reject_fact(request: web.Request) -> web.Response:
        fact_id = int(request.match_info["id"])
        if not await asyncio.to_thread(hand().reject, fact_id):
            raise web.HTTPNotFound(text="no such fact")
        core.state.emit("mind", what="fact", action="rejected", id=fact_id)
        return web.json_response({"ok": True})

    async def cancel_reminder(request: web.Request) -> web.Response:
        intent_id = int(request.match_info["id"])
        if not await asyncio.to_thread(hand().cancel, intent_id):
            raise web.HTTPNotFound(text="no such reminder")
        core.state.emit("mind", what="reminder", action="cancelled",
                        id=intent_id)
        return web.json_response({"ok": True})

    async def intents_held(request: web.Request) -> web.Response:
        if engine is None:
            return web.json_response({"intents": []})
        return web.json_response({"intents": [
            {"id": intent.id, "what": intent.what, "why": intent.why,
             "weight": intent.weight, "made": intent.made,
             "not_before": intent.not_before}
            for intent in engine.held()
        ]})

    async def intent_say(request: web.Request) -> web.Response:
        intent_id = int(request.match_info["id"])
        if engine is None or not engine.say_now(intent_id):
            raise web.HTTPNotFound(text="no such intent")
        return web.json_response({"ok": True})

    async def intent_drop(request: web.Request) -> web.Response:
        intent_id = int(request.match_info["id"])
        if engine is None or not engine.drop(intent_id):
            raise web.HTTPNotFound(text="no such intent")
        return web.json_response({"ok": True})

    async def save_profile(request: web.Request) -> web.Response:
        data = await body(request)
        try:
            await asyncio.to_thread(
                hand().save_profile, data.get("key"), data.get("text"))
        except ValueError as why:
            raise web.HTTPBadRequest(text=str(why)) from None
        core.state.emit("mind", what="profile", action="saved",
                        id=data.get("key"))
        return web.json_response({"ok": True})

    async def mind_journal(request: web.Request) -> web.Response:
        raw = request.query.get("limit", "50")
        try:
            limit = int(raw)
        except ValueError:
            raise web.HTTPBadRequest(text="limit must be an integer") from None
        limit = min(100, max(1, limit))
        try:
            answer = await asyncio.to_thread(hand().journal, limit)
        except RuntimeError as exc:
            raise web.HTTPServiceUnavailable(text=str(exc)) from None
        return web.json_response(answer)

    async def mind_search(request: web.Request) -> web.Response:
        query = request.query.get("q", "").strip()
        if not query:
            raise web.HTTPBadRequest(text="nothing to look for")
        if len(query) > 500:
            raise web.HTTPBadRequest(text="query is too long")
        try:
            answer = await hand().search(query, request.query.get("when", ""))
        except RuntimeError as exc:
            raise web.HTTPServiceUnavailable(text=str(exc)) from None
        return web.json_response(answer)

    # -- what the ear heard, to hear again and mark ------------------------

    async def heard_list(request: web.Request) -> web.Response:
        try:
            limit = max(1, min(200, int(request.query.get("limit", "60"))))
        except ValueError:
            raise web.HTTPBadRequest(text="limit is a number") from None
        return web.json_response(await asyncio.to_thread(recordings.replies, limit))

    async def heard_sound(request: web.Request) -> web.StreamResponse:
        path = recordings.sound(request.match_info["name"])
        if path is None:
            raise web.HTTPNotFound(text="no such recording")
        return web.FileResponse(path, headers={"Content-Type": "audio/wav"})

    async def heard_mark(request: web.Request) -> web.Response:
        data = await body(request)
        try:
            marked = await asyncio.to_thread(recordings.mark, request.match_info["turn"],
                                             data.get("label"), data.get("truth"))
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from None
        if not marked:
            raise web.HTTPNotFound(text="no such reply")
        return web.json_response({"ok": True, "marked": marked})

    # -- backups of the owner's memory ---------------------------------------

    async def backups_list(request: web.Request) -> web.Response:
        listed = await asyncio.to_thread(backup.listing, home)
        return web.json_response({"backups": listed, "restored": backup.restored(home),
                                  "folder": str(home / "backups")})

    async def backups_make(request: web.Request) -> web.Response:
        data = await body(request)
        made = await asyncio.to_thread(backup.make, home, sound=data.get("sound") is not False,
                                       version=build_info["version"])
        listed = await asyncio.to_thread(backup.listing, home)
        return web.json_response(next(b for b in listed if b["name"] == made.name))

    async def backups_get(request: web.Request) -> web.StreamResponse:
        path = home / "backups" / request.match_info["name"]
        if not path.is_file():
            raise web.HTTPNotFound(text="no such backup")
        return web.FileResponse(path, headers={
            "Content-Type": "application/zip",
            "Content-Disposition": f'attachment; filename="{path.name}"'})

    async def backups_bring(request: web.Request) -> web.Response:
        """A backup from another Mac, taken as it comes and checked before
        it is listed."""
        folder = home / "backups"
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        partial = folder / f".brought-{secrets.token_hex(4)}.zip"
        size = 0
        try:
            with partial.open("wb") as out:
                async for chunk in request.content.iter_chunked(1 << 20):
                    size += len(chunk)
                    if size > BACKUP_LIMIT:
                        raise web.HTTPRequestEntityTooLarge(max_size=BACKUP_LIMIT,
                                                            actual_size=size)
                    out.write(chunk)
            try:
                await asyncio.to_thread(backup.check, partial)
            except backup.BackupError as exc:
                raise web.HTTPBadRequest(text=str(exc)) from None
            partial.chmod(0o600)
            target = backup.free_name(folder)
            os.replace(partial, target)
        finally:
            partial.unlink(missing_ok=True)
        listed = await asyncio.to_thread(backup.listing, home)
        return web.json_response(next(b for b in listed if b["name"] == target.name))

    async def backups_restore(request: web.Request) -> web.Response:
        path = home / "backups" / request.match_info["name"]
        if not path.is_file():
            raise web.HTTPNotFound(text="no such backup")
        try:
            manifest = await asyncio.to_thread(backup.stage, home, path)
        except backup.BackupError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from None
        # The database is open here; the next start puts the backup in place
        # before anything opens it. The app (or Docker) starts the brain again.
        logger.warning("restoring from %s: the brain exits to take its place", path.name)
        asyncio.get_running_loop().call_later(0.5, os.kill, os.getpid(), signal.SIGTERM)
        return web.json_response({"ok": True, "made": manifest.get("made")})

    # -- a newer heare --------------------------------------------------------

    async def update_get(request: web.Request) -> web.Response:
        return web.json_response(updates.state)

    async def update_check(request: web.Request) -> web.Response:
        return web.json_response(await updates.check())

    async def update_install(request: web.Request) -> web.Response:
        try:
            return web.json_response(await updates.install())
        except UpdateError as exc:
            raise web.HTTPConflict(text=str(exc)) from None

    async def update_failed(request: web.Request) -> web.Response:
        """The app's word that it could not put the new version in."""
        data = await body(request)
        return web.json_response(updates.failed(str(data.get("why") or "")))

    async def update_machine(request: web.Request) -> web.Response:
        """The owner's button: this machine takes the newest release."""
        data = await body(request)
        machine = str(data.get("machine") or "")
        if not machine.startswith("machine@"):
            raise web.HTTPBadRequest(text="machine is a machine@ pid")
        try:
            return web.json_response(updates.ask_machine(machine))
        except UpdateError as exc:
            raise web.HTTPConflict(text=str(exc)) from None

    async def update_mine(request: web.Request) -> web.Response:
        """A machine's own question: is there a version for me to take?"""
        return web.json_response(updates.for_machine(
            request.get("part") or BRAIN_MACHINE, str(request.query.get("version") or ""),
            re.sub(r"[^a-z0-9_]", "", str(request.query.get("arch") or ""))[:16]))

    async def update_mine_failed(request: web.Request) -> web.Response:
        data = await body(request)
        return web.json_response(updates.machine_failed(
            request.get("part") or BRAIN_MACHINE, str(data.get("why") or "")))

    async def asking_for_updates(app: web.Application):
        async def every_day() -> None:
            await asyncio.sleep(UPDATE_FIRST_S)
            while True:
                if settings.update_check is not False:
                    await updates.check()
                await asyncio.sleep(UPDATE_EVERY_S)

        task = asyncio.create_task(every_day())
        yield
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def mind_forget(request: web.Request) -> web.Response:
        data = await body(request)
        try:
            forgotten = await hand().forget(data.get("ids"))
        except ValueError as why:
            raise web.HTTPBadRequest(text=str(why)) from None
        except RuntimeError as exc:
            raise web.HTTPServiceUnavailable(text=str(exc)) from None
        core.state.emit("mind", what="journal", action="forgotten",
                        count=len(data["ids"]))
        return web.json_response({"ok": True, "forgotten": forgotten})

    # -- screens ----------------------------------------------------------

    # A screen is model-written HTML. It runs with scripts (a screen may
    # animate itself), but in an opaque origin with no way to reach /api/*,
    # the person's cookie or the network. Model output never becomes code
    # in the pult's origin.
    SCREEN_CSP = ("sandbox allow-scripts; default-src 'none'; "
                  "script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                  "img-src data: blob:; font-src data:")

    def screen_path(request: web.Request) -> Path:
        name = request.match_info["name"]
        if not _NAME.fullmatch(name):
            raise web.HTTPBadRequest(text="a screen's name is lowercase letters and digits")
        return screens / f"{name}.html"

    async def list_screens(request: web.Request) -> web.Response:
        found = sorted(p.stem for p in screens.glob("*.html")) if screens.is_dir() else []
        return web.json_response(found)

    async def save_screen(request: web.Request) -> web.Response:
        path = screen_path(request)
        html = str((await body(request)).get("html", ""))
        if not html.strip():
            raise web.HTTPBadRequest(text="an empty screen")
        screens.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        core.state.emit("screen", name=path.stem)
        return web.json_response({"saved": path.stem})

    async def show_screen(request: web.Request) -> web.Response:
        path = screen_path(request)
        if not path.is_file():
            raise web.HTTPNotFound(text="no such screen")
        shown = handed(path.read_text(encoding="utf-8"))
        shown.headers["Content-Security-Policy"] = SCREEN_CSP
        return shown

    async def page(request: web.Request) -> web.Response:
        # The words come with the page: no second request, no English flash
        # before the catalog arrives.
        return handed(words.page(PAGE.read_text(encoding="utf-8")))

    async def font(request: web.Request) -> web.FileResponse:
        path = FONTS / f"{request.match_info['name']}.woff2"
        if not path.is_file():
            raise web.HTTPNotFound(text="no such font")
        return web.FileResponse(path, headers={
            "Content-Type": "font/woff2", "Cache-Control": "max-age=604800"})

    app = web.Application(middlewares=[guard], client_max_size=MAX_FILE_BYTES)
    app.on_startup.append(follow_tasks)
    app.cleanup_ctx.append(asking_for_updates)
    app.add_routes([
        web.get("/", page),
        web.get("/fonts/{name:[a-z-]+}.woff2", font),
        web.post("/api/pair", pair),
        web.post("/api/pair-code", pair_code),
        web.post("/api/message", message),
        web.get("/health", health),
        web.post("/api/parts", register),
        web.post("/api/machine/mine/report", machine_report),
        web.get("/api/machines", machines_list),
        web.put("/api/machines/{name}", machine_rename),
        web.post("/api/machines/{name}/forget", machine_forget),
        web.post("/api/machines/{name}/parts/{part}", machine_part),
        web.post("/api/machines/{name}/parts/{part}/restart", machine_restart),
        web.post("/api/bodies", register),  # the old name, still a part's knock
        web.post("/api/bodies/{id}/approve", approve),
        web.post("/api/bodies/{id}/reject", reject),
        web.post("/api/bodies/{id}/forget", forget),
        web.post("/api/executor/claim", claim),
        web.post("/api/executor/{id:\\d+}/step", step),
        web.post("/api/executor/{id:\\d+}/permit", executor_permit),
        web.post("/api/executor/{id:\\d+}/ask", executor_ask),
        web.post("/api/executor/{id:\\d+}/result", result),
        web.post("/api/input", input_event),
        web.post("/api/input/said_over", said_over),
        web.get("/api/events", events),
        web.get("/api/state", state),
        web.get("/api/history", history),
        web.get("/api/canvas", canvas),
        web.post("/api/canvas/put", canvas_put),
        web.post("/api/canvas/remove", canvas_remove),
        web.get("/api/system", machine),
        web.get("/api/controls", controls),
        web.post("/api/controls", control),
        web.get("/api/skills", skills_list),
        web.get("/api/prompt", system_prompt),
        web.get("/api/sessions", sessions_list),
        web.post("/api/sessions", session_new),
        web.post("/api/sessions/{id}/open", session_open),
        web.post("/api/sessions/{id}/rename", session_rename),
        web.delete("/api/sessions/{id}", session_delete),
        web.get("/api/sessions/{id}/turns", session_turns),
        web.post("/api/sessions/{id}/files", session_upload),
        web.get("/api/sessions/{id}/files/{file}", session_file),
        web.get("/api/context", context),
        web.post("/api/skills/call", skill_call),
        web.post("/api/skills/{id:\\d+}", skill_answer),
        web.post("/api/stop", stop),
        web.get("/api/tasks", tasks),
        web.post("/api/tasks", start_task),
        web.post("/api/tasks/{id:\\d+}/stop", stop_task),
        web.post("/api/permission/{id:\\d+}", permit),
        web.post("/api/answer/{id:\\d+}", answer),
        web.get("/api/settings", get_settings),
        web.post("/api/settings", set_settings),
        web.get("/api/models", models),
        web.get("/api/keys", get_keys),
        web.post("/api/keys", set_key),
        web.post("/api/keys/adopt", adopt_key),
        web.post("/api/keys/ask", key_ask),
        web.get("/api/keys/asked", key_asked),
        web.post("/api/keys/answer", key_answer),
        web.get("/api/memory", memory),
        web.get("/api/features", list_features),
        web.post("/api/features/{id:[a-z][a-z0-9_-]*}/act", act_feature),
        web.get("/api/mind", mind_snapshot),
        web.get("/api/mind/journal", mind_journal),
        web.get("/api/mind/search", mind_search),
        web.get("/api/heard", heard_list),
        web.get("/api/heard/{name}.wav", heard_sound),
        web.post("/api/heard/{turn}", heard_mark),
        web.post("/api/mind/journal/forget", mind_forget),
        web.get("/api/update", update_get),
        web.post("/api/update/check", update_check),
        web.post("/api/update/install", update_install),
        web.post("/api/update/failed", update_failed),
        web.post("/api/update/machine", update_machine),
        web.get("/api/update/mine", update_mine),
        web.post("/api/update/mine/failed", update_mine_failed),
        web.get("/api/backups", backups_list),
        web.post("/api/backups", backups_make),
        web.post("/api/backups/bring", backups_bring),
        web.get(f"/api/backups/{{name:{backup.NAME}}}", backups_get),
        web.post(f"/api/backups/{{name:{backup.NAME}}}/restore", backups_restore),
        web.post("/api/mind/facts/{id:\\d+}/confirm", confirm_fact),
        web.get("/api/mind/sources", sources_list),
        web.post("/api/mind/sources/{name}/import", source_import),
        web.post("/api/mind/sources/{name}/undo", source_undo),
        web.post("/api/mind/facts/{id:\\d+}/reject", reject_fact),
        web.post("/api/mind/reminders/{id:\\d+}/cancel", cancel_reminder),
        web.get("/api/intents", intents_held),
        web.post("/api/intents/{id:\\d+}/now", intent_say),
        web.post("/api/intents/{id:\\d+}/drop", intent_drop),
        web.put("/api/mind/profile", save_profile),
        web.get("/api/screens", list_screens),
        web.put("/api/screens/{name}", save_screen),
        web.get("/screens/{name}", show_screen),
    ])
    return app


def _sse(data: dict) -> bytes:
    return f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode()


def _requested(request: web.Request) -> frozenset[str] | None:
    """The status kinds a stream asked for, or None for the whole stream.

    An unknown name is dropped; a `kinds` that names nothing known leaves an
    empty set, not the whole stream: a filter that cannot be understood must
    not fall open."""
    raw = request.query.get("kinds")
    if raw is None:
        return None
    return frozenset(name.strip() for name in raw.split(",")
                     if name.strip() in STATUS_KINDS)


def _status_view(event: Event) -> dict:
    """One event as a status-only stream sees it: what to draw, never words."""
    if event.kind == "part":
        return event.view()
    fields = _STATUS_FIELDS[event.kind]
    return {"kind": event.kind, "at": event.at,
            **{name: event.data[name] for name in fields if name in event.data}}


async def serve(core: Core, settings: Any, vitals: Any = None,
                make_executor: Any = None, *, mind: Any = None,
                features: Any = None, engine: Any = None,
                show_bodies: Callable[[Seen], None] | None = None) -> web.AppRunner:
    """Start the API, on this machine only unless ``api_host`` says else.
    Returns the runner to stop it.

    Another host (a container's 0.0.0.0) opens nothing unguarded: every
    /api/ and /screens/ route asks for the master token or a paired key,
    the page carries no key, and pairing takes a one-time code."""
    from heare.api.canvas import Canvas

    core.add_output(Screen(core))
    core.add_output(VoiceOut(core))
    core.add_output(Canvas(Path(settings.canvas_file), core.state.emit))
    runner = web.AppRunner(build(core, settings, vitals,
                                 make_executor=make_executor, mind=mind,
                                 features=features, engine=engine,
                                 show_bodies=show_bodies))
    await runner.setup()
    host = str(settings.api_host or "127.0.0.1")
    await web.TCPSite(runner, host, int(settings.api_port)).start()
    return runner
