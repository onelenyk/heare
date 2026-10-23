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
import json
import logging
import re
import secrets
import socket
import subprocess
import time
from importlib import metadata
from pathlib import Path
from typing import Any, AsyncIterator

from aiohttp import web

from heare import keys
from heare.core import PROTOCOL, Core, Message
from heare.api import auth
from heare.api import pair as codes
from heare.api.registry import Registry
from heare.model.providers import PROVIDERS, list_models

logger = logging.getLogger("heare.api.server")

PAGE = Path(__file__).with_name("page.html")
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,60}")

# How often a quiet stream is written to. A closed page is only noticed when
# a write to it fails, so this is also how long it takes to show it left.
HEARTBEAT_S = 5.0
SESSION = "heare_session"


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
    """The voice body's side of the door: the reply, piece by piece, and the
    mouth's controls — pause, resume, stop — the body obeys from afar."""

    name = "voice"
    abilities: frozenset[str] = frozenset({"pause"})

    def __init__(self, core: Core) -> None:
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


def build(core: Core, settings: Any, vitals: Any = None,
          make_executor: Any = None, *, mind: Any = None,
          features: Any = None) -> web.Application:
    """vitals: () -> dict of what the app knows and the core does not —
    which model is answering, and whether it is the one that was chosen.

    mind and features are handed over by the brain. The app is still built
    without them: each surface then says so instead of failing."""
    secret = token(Path(settings.api_token_file))
    part_tokens = Path(settings.part_tokens_file)
    screens = Path(settings.screens_dir)
    tally = Tally(core)
    # Read once: the version cannot change under a running process.
    build_info = running()

    PART_CALLS = (
        ("POST", re.compile(r"^/api/bodies$")),
        ("POST", re.compile(r"^/api/input$")),
        ("POST", re.compile(r"^/api/input/said_over$")),
        ("GET", re.compile(r"^/api/events$")),
        ("POST", re.compile(r"^/api/executor/claim$")),
        ("POST", re.compile(r"^/api/executor/\d+/(step|permit|result|ask)$")),
    )

    def part_may(request: web.Request) -> bool:
        return any(request.method == method and rx.match(request.path)
                   for method, rx in PART_CALLS)

    @web.middleware
    async def guard(request: web.Request, handler):
        if request.path == "/api/pair":
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
            # A window has the person's powers, but is still one window: its
            # knock is its own, so two open pages are two parts, not one.
            request["window"] = pid
            return await handler(request)
        if pid.startswith("machine@"):
            if request.method == "POST" and request.path == "/api/bodies":
                request["part"] = pid
                return await handler(request)
            raise web.HTTPForbidden(text="a machine only knocks")
        if not part_may(request):
            raise web.HTTPForbidden(text="not for a part")
        request["part"] = pid
        return await handler(request)

    code_path = Path(settings.pair_codes_file)
    pace = Slow()
    minted_at: dict[str, float] = {}

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
        text = str((await body(request)).get("text", "")).strip()
        if not text:
            raise web.HTTPBadRequest(text="nothing was said")
        core.hear(Message(text=text, channel="screen"))
        return web.json_response({"ok": True})

    registry = Registry(
        Path(settings.parts_file), core.state.emit,
        (lambda channel, about: core.add_executor(make_executor(channel, about)))
        if make_executor is not None else None)
    registry.load(Path(settings.parts_dir))
    bodies = registry.bodies

    def own_channel(request: web.Request, channel: str) -> None:
        """A part may speak for its own channel and no other."""
        pid = request.get("part")
        if pid is not None and channel and channel != registry.channel_of(pid):
            raise web.HTTPForbidden(text="not your channel")

    async def claim(request: web.Request) -> web.Response:
        """A body's hands ask for work; the brain hands one out, or nothing."""
        data = await body(request)
        own_channel(request, str(data.get("name") or ""))
        found = core.executors.get(str(data.get("name") or ""))
        task = await found.claim() if hasattr(found, "claim") else None
        return web.json_response(task or {})

    async def step(request: web.Request) -> web.Response:
        data = await body(request)
        found = _executor_for(request)
        return web.json_response({"stopped": found.step(
            int(request.match_info["id"]), str(data.get("text", "")))})

    async def executor_permit(request: web.Request) -> web.Response:
        data = await body(request)
        found = _executor_for(request)
        allow, stopped = await found.permit(
            int(request.match_info["id"]),
            str(data.get("what", "")), str(data.get("kind", "change")),
        )
        return web.json_response({"allow": bool(allow), "stopped": bool(stopped)})

    async def executor_ask(request: web.Request) -> web.Response:
        """The agent's question mid-work: the person's answer comes back."""
        data = await body(request)
        found = _executor_for(request)
        answer = await found.ask(int(request.match_info["id"]),
                                 str(data.get("question", "")))
        return web.json_response({"answer": answer})

    async def result(request: web.Request) -> web.Response:
        data = await body(request)
        found = _executor_for(request)
        return web.json_response({"ok": found.result(
            int(request.match_info["id"]),
            str(data.get("status", "done")), str(data.get("result", "")),
        )})

    def _executor_for(request: web.Request):
        name = request.query.get("name") or ""
        own_channel(request, name)
        found = core.executors.get(name)
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
        return {"name": name} if name else {}

    def granted(request: web.Request, pid: str) -> dict:
        """A key of its own for a part that knocked with the master or a machine key.

        Never for a window: it already holds its key in a cookie, and a new
        one would replace it and send the page back to pairing."""
        who = request.get("part") or request.get("window")
        if who is not None and not who.startswith("machine@"):
            return {}
        return {"token": auth.issue(part_tokens, pid)}

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
        # A browser cannot name its machine; a window is called what it was
        # paired as, never after the brain's host.
        host = str(data.get("host") or (who.split("@", 1)[1] if window
                                        else socket.gethostname()))
        pid = who if window else str(data.get("key") or f"{channel}@{host}")
        if who is not None and who != pid and not who.startswith("machine@"):
            raise web.HTTPForbidden(text="not your name")
        version = data.get("v")
        if version is not None and version != PROTOCOL:
            # Said, not half understood: a part of another wire leaves with
            # the reason instead of talking past the brain.
            return web.json_response({"approved": False, "why": (
                f"мозок говорить протоколом v{PROTOCOL}, частина — v{version}")})
        hears = "all" if data.get("hears") == "all" else "own"
        # The part's own version: shown on its card, never a reason to trust.
        part_version = str(data["version"]) if data.get("version") else None
        forms = list(data.get("forms") or [])
        about = str(data.get("about") or "")
        ui = list(data.get("ui") or []) or list(
            (bodies.get(pid) or {}).get("ui") or [])
        if (pid in registry.trusted()
                or str(who or "").startswith(("window@", "machine@"))):
            registry.attach(pid, channel, forms, about, host, "approved", ui, hears,
                            version=part_version)
            return web.json_response({"approved": True, "pid": pid, **named(),
                                      **granted(request, pid)})
        registry.attach(pid, channel, forms, about, host, "pending", ui, hears,
                        version=part_version)
        decided = await registry.answer(pid)
        if decided == "expired":
            return web.json_response({"approved": False, "why": "ніхто не відповів"})
        if decided != "approved":
            return web.json_response({"approved": False, "why": decided})
        registry.attach(pid, channel, forms, about, host, "approved", ui, hears,
                        version=part_version)
        return web.json_response({"approved": True, "pid": pid, **named(),
                                  **granted(request, pid)})

    async def approve(request: web.Request) -> web.Response:
        return _decide(request, "approved")

    async def reject(request: web.Request) -> web.Response:
        return _decide(request, "rejected")

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
        core.executors.pop(str(entry.get("channel", "")), None)
        return web.json_response({"ok": True})

    def _decide(request: web.Request, state: str) -> web.Response:
        if not registry.decide(request.match_info["id"], state):
            raise web.HTTPNotFound(text="no such part is waiting")
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
            core.port.note(str(data.get("key", "")), data.get("value"))
        elif kind == "state":
            values = data.get("values")
            if not isinstance(values, dict):
                raise web.HTTPBadRequest(text="values must be an object")
            # A window reports for itself: several pages share the channel
            # "web", and one page's switch is not another's.
            entry = bodies.get(request.get("window") or "") or registry.by_channel(channel)
            if entry is None:
                raise web.HTTPNotFound(text="no such part")
            entry["values"].update(values)
            if channel == "voice" and "speaking" in values:
                out = core.outputs.get("voice")
                if isinstance(out, VoiceOut):
                    out.sounding(bool(values["speaking"]))
            core.state.emit("part", channel=entry["channel"],
                            forms=entry["forms"], about=entry["about"],
                            host=entry["host"], state=entry["state"])
        elif kind == "heard":
            text = str(data.get("text", "")).strip()
            if not text:
                raise web.HTTPBadRequest(text="nothing was said")
            core.port.heard(Message(
                text=text, channel=channel,
                addressed=bool(data.get("addressed", True)),
                speech_ms=data.get("speech_ms"),
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
        try:
            # The past only when asked for: a page that also loads the
            # history would otherwise show the last exchange twice.
            replay = max(0, min(int(request.query.get("replay", "0")), 200))
            for past in list(core.state.journal)[-replay:] if replay else ():
                if registry.hears(listener, past.data):
                    await response.write(_sse(past.view()))
            while True:
                try:
                    event = await asyncio.wait_for(reader.get(), HEARTBEAT_S)
                    if registry.hears(listener, event.data):
                        await response.write(_sse(event.view()))
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
            "running": build_info,
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
        target = bodies.get(part) or registry.by_channel(part)
        if target is not None:
            names = {str(c.get("name", "")) for c in target.get("ui") or []}
            if target.get("state") != "approved" or name not in names:
                raise web.HTTPNotFound(text="no such control")
            # Addressed to the part as well as the channel: pages share the
            # channel "web", and a switch on one page's card is that page's.
            pid = part if part in bodies else registry.pid_of(target)
            core.state.emit("control", channel=target["channel"], name=name,
                            value=value, part=pid)
            return web.json_response({"ok": True})
        try:
            now = core.control(part, name, value)
        except KeyError:
            raise web.HTTPNotFound(text="no such control") from None
        except (ValueError, TypeError) as exc:
            raise web.HTTPBadRequest(text=str(exc)) from None
        return web.json_response(now)

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
        await asyncio.to_thread(keys.store, name, value)
        core.state.emit("key", name=name)
        return web.json_response({"saved": name})

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

    # -- screens ----------------------------------------------------------

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
        return handed(path.read_text(encoding="utf-8"))

    async def page(request: web.Request) -> web.Response:
        return handed(PAGE.read_text(encoding="utf-8"))

    app = web.Application(middlewares=[guard])
    app.add_routes([
        web.get("/", page),
        web.post("/api/pair", pair),
        web.post("/api/pair-code", pair_code),
        web.post("/api/message", message),
        web.post("/api/bodies", register),
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
        web.get("/api/memory", memory),
        web.get("/api/features", list_features),
        web.post("/api/features/{id:[a-z][a-z0-9_-]*}/act", act_feature),
        web.get("/api/mind", mind_snapshot),
        web.get("/api/mind/journal", mind_journal),
        web.get("/api/mind/search", mind_search),
        web.post("/api/mind/facts/{id:\\d+}/confirm", confirm_fact),
        web.post("/api/mind/facts/{id:\\d+}/reject", reject_fact),
        web.post("/api/mind/reminders/{id:\\d+}/cancel", cancel_reminder),
        web.put("/api/mind/profile", save_profile),
        web.get("/api/screens", list_screens),
        web.put("/api/screens/{name}", save_screen),
        web.get("/screens/{name}", show_screen),
    ])
    return app


def _sse(data: dict) -> bytes:
    return f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode()


async def serve(core: Core, settings: Any, vitals: Any = None,
                make_executor: Any = None, *, mind: Any = None,
                features: Any = None) -> web.AppRunner:
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
                                 features=features))
    await runner.setup()
    host = str(settings.api_host or "127.0.0.1")
    await web.TCPSite(runner, host, int(settings.api_port)).start()
    return runner
