"""The core, opened to the outside: one API for every screen.

The built-in page uses exactly this and nothing else — no private door.
If the page that ships can be built on it, so can the ones a person
makes for themselves.

Nine groups: messages, events, state, controls, tasks, settings, keys,
memory, screens. Each is the core's own surface and holds no logic of its own.

A key can be written here and can never be read back: a screen a model
drew may save a token, and cannot fetch one.

Bound to this machine and guarded by a token the page is handed when it
is served, so a web page from somewhere else cannot drive the assistant.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from pathlib import Path
from typing import Any, AsyncIterator

from aiohttp import web

from heare import keys
from heare.core import Core, Message

PAGE = Path(__file__).with_name("page.html")
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,60}")
# Keys a screen may set. Named, so the API cannot be used to plant
# arbitrary secrets under the assistant's service.
KEY_NAMES = (
    "GROQ_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "OPENCODE_API_KEY",
    "ZAI_API_KEY",
)

# How long a part waits for the person's answer before it leaves.
APPROVAL_WAIT_S = 120.0


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

    def pause(self) -> None:
        self._core.state.emit("voice_paused", channel=self.name)

    def resume(self) -> None:
        self._core.state.emit("voice_resumed", channel=self.name)

    def stop(self) -> None:
        self._stop = True
        self._core.state.emit("voice_stopped", channel=self.name)


def build(core: Core, settings: Any, vitals: Any = None,
          make_executor: Any = None) -> web.Application:
    """vitals: () -> dict of what the app knows and the core does not —
    which model is answering, and whether it is the one that was chosen."""
    secret = token(Path(settings.api_token_file))
    screens = Path(settings.screens_dir)
    tally = Tally(core)

    @web.middleware
    async def guard(request: web.Request, handler):
        if request.path.startswith("/api/"):
            given = request.headers.get("X-Heare-Token") or request.query.get("token")
            if not given or not secrets.compare_digest(given, secret):
                raise web.HTTPUnauthorized(text="no token")
        return await handler(request)

    async def body(request: web.Request) -> dict:
        try:
            data = await request.json()
        except (ValueError, json.JSONDecodeError):
            raise web.HTTPBadRequest(text="not JSON") from None
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(text="an object is expected")
        return data

    def handed(html: str) -> web.Response:
        """A page, with the token it needs to be a client of this API."""
        script = f"<script>window.HEARE_TOKEN={json.dumps(secret)}</script>"
        return web.Response(text=script + html, content_type="text/html")

    # -- messages ---------------------------------------------------------

    async def message(request: web.Request) -> web.Response:
        text = str((await body(request)).get("text", "")).strip()
        if not text:
            raise web.HTTPBadRequest(text="nothing was said")
        core.hear(Message(text=text, channel="screen"))
        return web.json_response({"ok": True})

    bodies: dict[str, dict] = {}
    waiting: dict[str, asyncio.Event] = {}

    async def claim(request: web.Request) -> web.Response:
        """A body's hands ask for work; the brain hands one out, or nothing."""
        data = await body(request)
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

    async def result(request: web.Request) -> web.Response:
        data = await body(request)
        found = _executor_for(request)
        return web.json_response({"ok": found.result(
            int(request.match_info["id"]),
            str(data.get("status", "done")), str(data.get("result", "")),
        )})

    def _executor_for(request: web.Request):
        name = request.query.get("name") or ""
        found = core.executors.get(name)
        if not hasattr(found, "claim"):
            raise web.HTTPNotFound(text="no such body")
        return found

    trusted_file = Path(settings.parts_file)

    def trusted() -> set[str]:
        try:
            return set(json.loads(trusted_file.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return set()

    def remember(pid: str) -> None:
        trusted_file.parent.mkdir(parents=True, exist_ok=True)
        trusted_file.write_text(
            json.dumps(sorted(trusted() | {pid}), ensure_ascii=False),
            encoding="utf-8",
        )

    def attach(pid: str, channel: str, forms: list, about: str,
               host: str, state: str) -> None:
        bodies[pid] = {"channel": channel, "forms": forms, "about": about,
                       "host": host, "state": state}
        if state == "approved" and "executor" in forms and make_executor is not None:
            core.add_executor(make_executor(channel, about))
        core.state.emit("part", channel=channel, forms=forms, about=about,
                        host=host, state=state)

    async def register(request: web.Request) -> web.Response:
        """A part knocks; the person decides whether to trust it.

        The request waits for that answer: a part not trusted is not used, and
        a part nobody answered leaves rather than lurk."""
        data = await body(request)
        channel = str(data.get("channel") or "").strip()
        if not channel:
            raise web.HTTPBadRequest(text="a part needs a channel")
        host = str(data.get("host") or "")
        pid = str(data.get("key") or f"{channel}@{host or '?'}")
        forms = list(data.get("forms") or [])
        about = str(data.get("about") or "")
        if pid in trusted():
            attach(pid, channel, forms, about, host, "approved")
            return web.json_response({"approved": True})
        attach(pid, channel, forms, about, host, "pending")
        waiting[pid] = asyncio.Event()
        try:
            await asyncio.wait_for(waiting[pid].wait(), APPROVAL_WAIT_S)
        except TimeoutError:
            bodies[pid]["state"] = "expired"
            return web.json_response({"approved": False, "why": "ніхто не відповів"})
        finally:
            waiting.pop(pid, None)
        if bodies[pid]["state"] != "approved":
            return web.json_response({"approved": False, "why": bodies[pid]["state"]})
        attach(pid, channel, forms, about, host, "approved")
        return web.json_response({"approved": True})

    async def approve(request: web.Request) -> web.Response:
        return _decide(request, "approved")

    async def reject(request: web.Request) -> web.Response:
        return _decide(request, "rejected")

    def _decide(request: web.Request, state: str) -> web.Response:
        pid = request.match_info["id"]
        entry = bodies.get(pid)
        if entry is None or entry.get("state") != "pending":
            raise web.HTTPNotFound(text="no such part is waiting")
        entry["state"] = state
        if state == "approved":
            remember(pid)
        event = waiting.get(pid)
        if event is not None:
            event.set()
        return web.json_response({"ok": True})

    async def input_event(request: web.Request) -> web.Response:
        """A body's side: what it heard, and what it did with it."""
        data = await body(request)
        kind = str(data.get("kind", ""))
        channel = str(data.get("channel") or "text")
        if kind == "started":
            core.port.started(channel)
        elif kind == "nothing":
            core.port.nothing(channel)
        elif kind == "overheard":
            core.port.overheard(channel, str(data.get("text", "")))
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
        turn = core.port.said_over(
            str(data.get("channel") or "text"),
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
        try:
            # The past only when asked for: a page that also loads the
            # history would otherwise show the last exchange twice.
            replay = max(0, min(int(request.query.get("replay", "0")), 200))
            for past in list(core.state.journal)[-replay:] if replay else ():
                await response.write(_sse(past.view()))
            while True:
                try:
                    event = await asyncio.wait_for(reader.get(), 15.0)
                    await response.write(_sse(event.view()))
                except TimeoutError:
                    await response.write(b": still here\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            core.state.unsubscribe(reader)
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
        try:
            now = core.control(str(data.get("part", "")), str(data.get("name", "")),
                               data.get("value"))
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

    async def get_keys(request: web.Request) -> web.Response:
        """Which keys exist. Never what they are."""
        return web.json_response({name: bool(keys.get(name)) for name in KEY_NAMES})

    async def set_key(request: web.Request) -> web.Response:
        data = await body(request)
        name, value = str(data.get("name", "")), str(data.get("value", "")).strip()
        if name not in KEY_NAMES or not value:
            raise web.HTTPBadRequest(text="not a key this assistant uses")
        await asyncio.to_thread(keys.store, name, value)
        core.state.emit("key", name=name)
        return web.json_response({"saved": name})

    # -- memory -----------------------------------------------------------

    async def memory(request: web.Request) -> web.Response:
        query = request.query.get("q", "").strip()
        if core.memory is None or not query:
            raise web.HTTPBadRequest(text="nothing to look for")
        found = await core.memory.recall(query, request.query.get("when", ""))
        return web.json_response({"said": found})

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
        web.post("/api/message", message),
        web.post("/api/bodies", register),
        web.post("/api/bodies/{id}/approve", approve),
        web.post("/api/bodies/{id}/reject", reject),
        web.post("/api/executor/claim", claim),
        web.post("/api/executor/{id:\\d+}/step", step),
        web.post("/api/executor/{id:\\d+}/permit", executor_permit),
        web.post("/api/executor/{id:\\d+}/result", result),
        web.post("/api/input", input_event),
        web.post("/api/input/said_over", said_over),
        web.get("/api/events", events),
        web.get("/api/state", state),
        web.get("/api/history", history),
        web.get("/api/canvas", canvas),
        web.post("/api/canvas/remove", canvas_remove),
        web.get("/api/system", machine),
        web.get("/api/controls", controls),
        web.post("/api/controls", control),
        web.post("/api/stop", stop),
        web.get("/api/tasks", tasks),
        web.post("/api/tasks", start_task),
        web.post("/api/tasks/{id:\\d+}/stop", stop_task),
        web.post("/api/permission/{id:\\d+}", permit),
        web.get("/api/settings", get_settings),
        web.post("/api/settings", set_settings),
        web.get("/api/keys", get_keys),
        web.post("/api/keys", set_key),
        web.get("/api/memory", memory),
        web.get("/api/screens", list_screens),
        web.put("/api/screens/{name}", save_screen),
        web.get("/screens/{name}", show_screen),
    ])
    return app


def _sse(data: dict) -> bytes:
    return f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode()


async def serve(core: Core, settings: Any, vitals: Any = None,
                make_executor: Any = None) -> web.AppRunner:
    """Start the API on this machine only. Returns the runner to stop it."""
    from heare.api.canvas import Canvas

    core.add_output(Screen(core))
    core.add_output(VoiceOut(core))
    core.add_output(Canvas(Path(settings.canvas_file), core.state.emit))
    runner = web.AppRunner(build(core, settings, vitals,
                                 make_executor=make_executor))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", int(settings.api_port)).start()
    return runner
