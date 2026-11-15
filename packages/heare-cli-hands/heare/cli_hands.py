"""The hands body: the agent that does the work, connected over the door.

    uv run python -m heare.cli_hands

A body, not the brain: it holds no memory and decides nothing. It claims a
piece of work, drives opencode's server through it, asks about changes, and
returns what came of it. Stop it and the brain keeps running.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import getpass
import json
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


from heare import layout, logs
from heare import hand, keys, part
from heare.config import Settings, machine_host
from heare.reach import changes, runner
from heare.reach.agents import KNOWN as AGENTS, MODE, MODES, Agent
from heare.reach.terminal import Terminal
from heare.core.i18n import N_, _

logger = logging.getLogger("heare.hands")

# A card is drawn once; a page that is slow to take it must not hold the
# work's result behind it.
SHOW_S = 20.0

# run() returns this when the person changed the runner: main() starts the
# hands again, so the agent is built once from settings, never patched live.
RESTART = -1

# Which control sets which setting. The hands own their runner; the page only
# draws what is declared here and hands the person's choice back.
_SETTINGS = {
    "mode": "executor_mode",
    "type": "executor",
    "binary": "executor_binary",
    "model": "executor_model",
    "auth": "executor_auth",
    "auth_env": "executor_auth_env",
    "base_url": "executor_base_url",
    "session": "executor_session",
    "cwd": "executor_cwd",
}


def _short(value: object, width: int = 70) -> str:
    text = " ".join(str(value).split())
    return text[:width] + "…" if len(text) > width else text


# What a step of the agent's own plan looks like on the card.
_MARKS = {"completed": "✓", "in_progress": "▸", "cancelled": "—"}


def _todo_lines(todo: list[dict]) -> str:
    """The agent's plan, one line a step, as a person reads it."""
    return "\n".join(
        f"{_MARKS.get(str(step.get('status') or ''), '•')} {step.get('content')}"
        for step in todo if step.get("content")
    )


def _price(cost: float, used: int, size: int = 0) -> str:
    """What the last turn cost, and how many tokens went into it. Free work
    shows its tokens alone: «$0.0000» is not a price worth reading."""
    if not cost and not used:
        return ""
    tokens = f"{used // 1000}k" if used >= 1000 else str(used)
    if not cost:
        return tokens
    if not size:
        return f"${cost:.4f} · {tokens}"
    window = f"{size // 1_000_000}M" if size >= 1_000_000 else f"{size // 1000}k"
    return f"${cost:.4f} · {tokens} / {window}"


# A stream's names are long; the card has one line for each.
_SHORT = {
    "message.part.updated": "part", "message.part.delta": "delta",
    "message.updated": "message", "session.status": "status",
    "session.idle": "idle", "session.diff": "diff",
    "permission.asked": "permission", "permission.replied": "permitted",
    "question.asked": "question", "question.replied": "answered",
}


def _line(direction: str, message: dict) -> str:
    """One call or one event, as a person reads it."""
    arrow = "→" if direction == "out" else "←"
    if direction == "out":
        method = str(message.get("method") or "?")
        return f"{arrow} {method} {message.get('path') or ''}".rstrip()
    kind = _SHORT.get(str(message.get("type") or "?"),
                      str(message.get("type") or "?"))
    props = message.get("properties")
    props = props if isinstance(props, dict) else {}
    part = props.get("part")
    part = part if isinstance(part, dict) else {}
    detail = (props.get("permission") or part.get("tool")
              or (part.get("state") or {}).get("input", {}).get("command")
              or (part.get("text") if part.get("type") == "text" else ""))
    return f"{arrow} {kind}" + (f" · {_short(detail)}" if detail else "")


def _kind(settings: Settings) -> str:
    kind = str(settings.executor or "opencode").strip()
    return kind if kind in runner.KNOWN else "opencode"


def _settings_ui(settings: Settings, sessions: list[dict] | None = None) -> list[dict]:
    """The runner's settings as controls, read on this machine.

    Read here and not by the brain: the binary, the sign-in and the models
    are facts of the machine the hands run on, which need not be the
    brain's.
    """
    kind = _kind(settings)
    binary = str(settings.executor_binary or "").strip()
    resolved = runner.resolve(binary or runner.KNOWN[kind]["binary"])
    model = str(settings.executor_model or "").strip()
    auth = str(settings.executor_auth or "login").strip()
    auth_env = str(settings.executor_auth_env or "").strip()

    runners = [{"value": name, "label": entry["label"]}
               for name, entry in runner.KNOWN.items()]
    ui: list[dict] = [
        {"name": "type", "kind": "choice" if len(runners) > 1 else "label",
         "label": N_("Type"), "value": kind, **({"options": runners} if len(runners) > 1 else {})},
        {"name": "binary", "kind": "text", "label": N_("Binary"), "value": binary,
         "hint": N_("empty — find it yourself")},
        {"name": "cwd", "kind": "text", "label": N_("Folder"),
         "value": str(settings.executor_cwd or ""),
         "hint": N_("empty — ~/heare-work; a real project folder works too")},
        {"name": "found", "kind": "label", "label": N_("Found"),
         "value": resolved or N_("not found")},
        {"name": "home", "kind": "label", "label": N_("Home"),
         "value": str(runner.agent_home())},
    ]
    listed = [m["id"] for group in runner.models(kind, resolved)
              for m in group["models"]]
    options = [{"value": "", "label": N_("— as in the CLI —")}] + [
        {"value": id_, "label": id_} for id_ in listed]
    if model and model not in listed:
        options.append({"value": model, "label": model})
    if listed:
        ui.append({"name": "model", "kind": "choice", "label": N_("Model"),
                   "value": model, "options": options})
    else:
        ui.append({"name": "model", "kind": "text", "label": N_("Model"),
                   "value": model, "hint": N_("the list did not come — type the id")})
    ui.append({"name": "auth", "kind": "choice", "label": N_("Sign-in"), "value": auth,
               "options": [{"value": "login", "label": N_("CLI login")},
                           {"value": "key", "label": N_("Own token")}]})
    options = [{"value": "", "label": N_("the last one")}] + [
        {"value": str(item.get("id") or ""),
         "label": f"{item.get('title') or item.get('id')} · {_when(item)}"}
        for item in sessions or [] if item.get("id")]
    ui.append({"name": "session", "kind": "choice",
               "label": N_("Continue"), "value": str(settings.executor_session or ""),
               "options": options})
    if auth == "key":
        # The key is read here, on the hands' machine. The brain's own key
        # store is another machine's once the brain lives elsewhere, so the
        # way to add one is said in the terms of this machine.
        source = keys.source(auth_env) if auth_env else None
        missing = (f"missing on {socket.gethostname()}: heare-cli-hands key {auth_env}"
                   if auth_env else N_("pick a key"))
        ui += [
            {"name": "auth_env", "kind": "choice", "label": N_("Key"),
             "value": auth_env,
             "options": [{"value": "", "label": "—"}]
             + [{"value": name, "label": name} for name in keys.NAMES]},
            {"name": "key", "kind": "label", "label": N_("Token"),
             "value": {"env": N_("present (from the environment)"), "stored": N_("present (in Keychain)")}.get(
                 source or "", missing)},
        ]
    else:
        ui.append({"name": "login", "kind": "label", "label": N_("Login"),
                   "value": runner.login(kind, resolved)["summary"]})
    ui += [
        {"name": "base_url", "kind": "text", "label": "Base URL",
         "value": str(settings.executor_base_url or ""),
         "hint": N_("empty — the native endpoint; only for a proxy")},
        {"name": "check", "kind": "button", "label": N_("Check")},
        {"name": "checks", "kind": "log", "label": N_("Checks"), "value": ""},
    ]
    for control in ui:
        control["group"] = "settings"
    return ui


def _setting(name: str, value: object) -> tuple[str, str] | None:
    """The setting a control changes, or None when it is not one it takes."""
    setting = _SETTINGS.get(name)
    if setting is None or not isinstance(value, str):
        return None
    value = value.strip()
    if name == "type" and value not in runner.KNOWN:
        return None
    if name == "mode" and value not in MODES:
        return None
    if name == "auth" and value not in ("login", "key"):
        return None
    if name == "auth_env" and value and value not in keys.NAMES:
        return None
    if name == "cwd" and value and not Path(value).expanduser().is_dir():
        return None  # a folder that is not there is not a folder to work in
    return setting, value


def _checks(settings: Settings) -> str:
    """What N_("Check") shows: one line per thing the runner needs."""
    kind = _kind(settings)
    health = runner.health(
        kind,
        str(settings.executor_binary or "").strip() or None,
        str(settings.executor_auth or "login"),
        str(settings.executor_auth_env or ""),
        str(settings.executor_base_url or ""),
        str(settings.executor_model or ""),
    )
    return "\n".join(f"{'✓' if c['ok'] else '✗'} {c['name']}: {c['detail']}"
                     for c in health["checks"])


SHOW_CHARS = 40_000


def _mcp(socket: Path) -> dict:
    """The agent's way back to us: one MCP server, one tool — show."""
    return {"heare": {"type": "local",
                      "command": [sys.executable, "-m", "heare.reach.bridge",
                                  "--socket", str(socket)],
                      "enabled": True}}


def _card_body(request: dict) -> dict:
    """What the agent asked to show, as the door takes it."""
    title = str(request.get("title") or "").strip()[:200]
    say = str(request.get("say") or "").strip()[:400]
    url = str(request.get("url") or "").strip()
    if url:
        return {"kind": "link", "url": url, "title": title, "say": say}
    html = str(request.get("html") or "")
    if not html.strip():
        return {}
    return {"kind": "html", "html": html[:SHOW_CHARS], "title": title, "say": say}


async def _shown(job: int, request: dict, show) -> str:
    """A card the agent asked for, carried through the door. Answers in the
    agent's words: short, and true when it did not happen."""
    body = _card_body(request)
    if not body:
        return "Failed: nothing to show."
    try:
        await show(job, body)
    except Exception as exc:  # noqa: BLE001 — a card is a nicety, not the work
        logger.warning("the card of job %d was not shown: %r", job, exc)
        return f"Failed: {exc}"
    return "Shown."


def _agent(settings: Settings, tap, mcp: dict | None = None) -> Agent | None:
    kind = str(settings.executor or "opencode").strip()
    entry = runner.KNOWN.get(kind)
    if entry is None:
        print(_("unknown agent {kind}; known: {known}").format(kind=kind, known=", ".join(runner.KNOWN)),
              file=sys.stderr)
        return None

    setting = str(settings.executor_binary or "").strip()
    resolved = runner.resolve(setting or entry["binary"])
    if resolved is None:
        print(_("no agent {kind}: {binary} not found — set the path in the settings").format(
            kind=kind, binary=setting or entry["binary"]), file=sys.stderr)
        return None

    # The server is started from this binary; the hands add the port.
    command = [resolved] + list(entry["command"][1:])

    model = str(settings.executor_model or "").strip()
    auth = str(settings.executor_auth or "login").strip()
    auth_env = str(settings.executor_auth_env or "").strip()
    base_url = str(settings.executor_base_url or "").strip()
    env = runner.env(kind, auth, auth_env, base_url, model)
    patch = runner.opencode_config_patch(kind, base_url, model)
    if mcp:
        patch = {**patch, "mcp": mcp}

    known = next((k for k in AGENTS if k.name == kind), None)
    if known is None:
        print(_("agent {kind} is not in the table").format(kind=kind), file=sys.stderr)
        return None
    return Agent(
        known,
        cwd=Path(str(settings.executor_cwd)).expanduser() if settings.executor_cwd else None,
        command=command,
        env=env,
        model=model or None,
        config_patch=patch or None,
        tap=tap,
        mode=_mode(settings),
    )


# The modes as the card names them.
_MODE_LABELS = {"careful": N_("Careful — asks about every change"),
                "normal": N_("Normal — weighs itself whether to ask"),
                "auto": N_("Auto — asks nothing")}


def _taking(settings: Settings) -> bool:
    value = getattr(settings, "hands_taking", True)
    return True if value == "" else bool(value)


def _take_work(settings: Settings, value: object) -> bool:
    on = bool(value)
    settings.set("hands_taking", on)
    return on


def _mode(settings: Settings) -> str:
    mode = str(getattr(settings, "executor_mode", "") or "").strip()
    return mode if mode in MODES else MODE


def _when(item: dict) -> str:
    """When a piece of work was last touched, as a card shows it."""
    stamp = (item.get("time") or {}).get("updated") or 0
    if not stamp:
        return "?"
    return time.strftime("%d %b %H:%M", time.localtime(stamp / 1000))


def _session_lines(found: list[dict]) -> str:
    """The hands' work, newest first, as a person reads it on the card."""
    return "\n".join(
        f"{_when(item)} · {item.get('title') or item.get('slug') or item.get('id')}"
        for item in found)


def _resume_from(job: hand.Job, settings: Settings, last: str) -> str:
    """Which session the work goes on with: the one it names, the one the
    card picked, or the last piece of work."""
    if job.session:
        return job.session
    if not job.continues:
        return ""
    return str(getattr(settings, "executor_session", "") or "") or last


def _recent_lines(found: list[dict]) -> str:
    """What the hands did lately, as a model reads it: title, when, and the
    id needed to go on with it."""
    lines = []
    for item in found:
        stamp = (item.get("time") or {}).get("updated") or 0
        when = time.strftime("%d %b %H:%M", time.localtime(stamp / 1000)) \
            if stamp else "?"
        title = str(item.get("title") or item.get("slug") or item.get("id"))
        lines.append(f"- {title} ({item.get('id')}, {when})")
    return "\n".join(lines)


async def _verb(agent: Agent, job: hand.Job, desk: hand.Desk) -> str:
    """An order the hands know by name: done here, without a model."""
    if job.verb == "sessions":
        found = await agent.recent()
        if not found:
            return "No work has been done in this folder yet."
        return "Recent work, newest first:\n" + _recent_lines(found)
    if job.verb == "undo":
        return await agent.undo()
    if job.verb == "redo":
        return await agent.redo()
    if job.verb == "plan":
        # The same life as work, in the agent that only looks: the plan
        # comes back as words and nothing on disk moves.
        return await agent.run(job.request, desk, plan=True)
    return _("I do not know that order: {verb}.").format(verb=job.verb)


async def _shown_changes(agent: Agent, job: int, show) -> str:
    """What the task changed: a card in the chat, and one sentence to say.

    A page that will not take the card is not a failed task: the words still
    go, and the person still hears what came of it."""
    found = await agent.changes()
    if not found:
        return ""
    try:
        await show(job, changes.card(found))
    except Exception:  # noqa: BLE001 — a card is a nicety, not the work
        logger.warning("the card of job %d was not shown", job)
    return changes.said(found)


def _sweep(folder: Path) -> None:
    """Sockets left by hands that were killed rather than let go: a hands'
    socket is removed on the way out, and a kill gives it none."""
    for path in folder.glob("hands-*.sock"):
        try:
            pid = int(path.stem.removeprefix("hands-"))
            os.kill(pid, 0)
        except ValueError:
            continue
        except ProcessLookupError:
            path.unlink(missing_ok=True)
        except PermissionError:
            continue


async def run() -> int:
    settings = Settings()
    session_file = Path(settings.parts_file).parent / "agent-session.json"
    try:
        resume = session_file.read_text(encoding="utf-8").strip()
    except OSError:
        resume = ""

    api: collections.deque[str] = collections.deque(maxlen=40)
    screen = Terminal()
    usage = {"cost": 0.0, "used": 0, "size": 0}
    told = 0

    def tap(direction: str, message: dict) -> None:
        nonlocal told
        if direction == "in":
            written = screen.written
            screen.feed(message)
            if screen.written != written:
                told += 1
        line = _line(direction, message)
        if line:
            api.append(line)
            told += 1

    folder = Path(settings.parts_file).parent
    _sweep(folder)
    socket_path = folder / f"hands-{os.getpid()}.sock"
    agent = _agent(settings, tap, _mcp(socket_path))
    # Hands without an agent still knock: their settings are the only place
    # a wrong path can be put right, and they take no work meanwhile.
    name = agent.name if agent is not None else _kind(settings)
    about = agent.about if agent is not None else N_("no agent found — see ⚙")
    forms = ["executor"] if agent is not None else []
    recent = await agent.recent() if agent is not None else []
    ui = [
        {"name": "taking", "kind": "toggle", "label": N_("Take work"),
         "value": _taking(settings)},
        {"name": "mode", "kind": "choice", "label": N_("Mode"), "value": _mode(settings),
         "options": [{"value": m, "label": _MODE_LABELS[m]} for m in MODES]},
        {"name": "agent", "kind": "label", "label": N_("Agent"), "value": name},
        {"name": "work", "kind": "label", "label": N_("State"), "head": True,
         "value": N_("waiting") if agent is not None else N_("not ready")},
        {"name": "usage", "kind": "label", "label": N_("Price"), "value": "—"},
        {"name": "plan", "kind": "log", "label": N_("Plan"), "value": ""},
        {"name": "sessions", "kind": "log",
         "label": N_("Agent's sessions"), "value": ""},
        {"name": "terminal", "kind": "terminal", "label": N_("Terminal"), "value": ""},
        {"name": "api", "kind": "log", "label": "API", "value": "",
         "group": "settings"},
    ] + await asyncio.to_thread(_settings_ui, settings, recent)
    values = {c["name"]: c["value"] for c in ui if "value" in c}
    restart = asyncio.Event()
    held: dict = {}

    async def refresh_price() -> None:
        """What the task cost, from the server: the bill is per session."""
        found = await agent.price() if agent is not None else {}
        tokens = found.get("tokens") or {}
        usage["cost"] = float(found.get("cost") or 0.0)
        usage["used"] = sum(int(tokens.get(key) or 0)
                            for key in ("input", "output", "reasoning"))

    async def check() -> None:
        values["checks"] = await asyncio.to_thread(_checks, Settings())
        held["wire"].report(values)

    def on_control(event: dict) -> None:
        if event.get("kind") != "control" or "wire" not in held:
            return
        control, value = event.get("name"), event.get("value")
        if control == "taking":
            values["taking"] = _take_work(settings, value)
        elif control == "check":
            asyncio.create_task(check())
            return
        elif (change := _setting(str(control), value)) is not None:
            settings.set(*change)
            values[str(control)] = change[1]
            values["work"] = N_("will apply the settings once free")
            restart.set()
        else:
            return
        held["wire"].report(values)

    async def work(wire: part.Wire) -> int:
        nonlocal resume, told
        held["wire"] = wire
        wire.report(values)
        sent = 0

        async def show(job: int, body: dict) -> dict:
            answer = await wire.post(f"/api/executor/{job}/show", body,
                                     timeout=SHOW_S)
            if answer.status_code != 200:
                raise RuntimeError(answer.text[:200] or "the page did not take it")
            return answer.json()

        current: dict = {"job": 0}

        async def handle_show(reader, writer) -> None:
            """The agent's MCP server, asking us to show something."""
            try:
                request = json.loads((await reader.readline()) or b"{}")
            except ValueError:
                request = {}
            answer = ""
            job = int(current.get("job") or 0)
            if request.get("tool") == "show" and job:
                answer = await _shown(job, request, show)
            writer.write((json.dumps({"answer": answer},
                                     ensure_ascii=False) + "\n").encode())
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        socket_path.unlink(missing_ok=True)
        held["asks"] = await asyncio.start_unix_server(
            handle_show, path=str(socket_path))

        async def refresh_card() -> None:
            nonlocal sent, told
            ticks = 0

            async def refresh_plan() -> None:
                """What the agent means to do next, from its own list."""
                values["plan"] = _todo_lines(await agent.todo()) \
                    if agent is not None else ""

            async def refresh_sessions() -> None:
                """What the hands have done, as a list a person can read."""
                values["sessions"] = _session_lines(await agent.recent()) \
                    if agent is not None else ""

            while True:
                await asyncio.sleep(0.4)
                ticks += 1
                if ticks % 5 == 0 and str(values.get("work", "")).startswith("working"):
                    await refresh_plan()
                    told += 1
                if ticks % 10 == 0:
                    await refresh_sessions()
                    told += 1
                if told != sent:
                    sent = told
                    values["api"] = "\n".join(api)
                    values["terminal"] = screen.text()
                    values["usage"] = _price(**usage) or values["usage"]
                    wire.report(values)

        def begin(job: hand.Job, desk: hand.Desk) -> None:
            logs.say(_("taken {id}: {what}").format(id=job.id, what=job.request[:60]))
            values["work"] = f"working: {job.request[:50]}"
            wire.report(values)
            screen.begin(job.request, new=not job.continues, asked=job.asked)
            current["job"] = job.id

        async def do(job: hand.Job, desk: hand.Desk) -> str:
            # An order is not work for the agent: no prompt, no session.
            if job.verb and job.verb != "fork":
                return await _verb(agent, job, desk)
            # Named work goes on with the session it names; work that goes on
            # from the last keeps it; anything new starts clean.
            chosen = _resume_from(job, settings, resume)
            if job.verb == "fork":
                if not await agent.branch(chosen):
                    return _("Could not branch the work.")
                chosen = agent.last_session
            try:
                result = await agent.run(job.request, desk, resume=chosen)
            except BaseException:
                # Work that broke halfway may still have changed files.
                await _shown_changes(agent, job.id, show)
                raise
            said = await _shown_changes(agent, job.id, show)
            return f"{result} {said}".strip() if said else result

        def end(job: hand.Job, status: str, result: str) -> None:
            nonlocal resume, told
            screen.end(status, result)
            current["job"] = 0
            told += 1
            logs.say(_("done {id}: {status} {what}").format(id=job.id, status=status, what=result[:60]))
            if agent.last_session:
                resume = agent.last_session
                try:
                    session_file.write_text(agent.last_session, encoding="utf-8")
                except OSError:
                    pass
            if not restart.is_set():
                values["work"] = N_("waiting")
            wire.report(values)
            asyncio.create_task(refresh_price())

        wiring = asyncio.create_task(refresh_card())
        try:
            while True:
                # A claim already in flight is let finish, so work the brain
                # handed out is never dropped by a restart.
                if restart.is_set():
                    return RESTART
                if agent is None or not values["taking"]:
                    try:
                        await asyncio.wait_for(restart.wait(), 1.0)
                    except TimeoutError:
                        pass
                    continue
                if not await hand.take(wire, do, on_permit=screen.permit,
                                       on_begin=begin, on_end=end):
                    await asyncio.sleep(hand.IDLE_S)
        finally:
            wiring.cancel()
            await asyncio.gather(wiring, return_exceptions=True)

    host = machine_host()
    try:
        # The channel is the agent's name. Switching the runner type
        # therefore changes the channel, while the away card in the
        # manifest still says "opencode"; the manifest is the lead's
        # decision, not this part's.
        return await part.live(
            {"channel": name, "forms": forms, "about": about,
             "key": f"{name}@{host}", "host": host, "ui": ui},
            work, on_event=on_control, hello=_("hands: {name}").format(name=name), settings=settings)
    finally:
        server = held.get("asks")
        if server is not None:
            server.close()
            with contextlib.suppress(Exception):
                await server.wait_closed()
        socket_path.unlink(missing_ok=True)
        if agent is not None:
            # A restart stays in this process; the old agent must not.
            await agent.close()


def _agent_login() -> int:
    """Sign the agent in to its own opencode home.

    The home is ours, so the person's own sign-in does not count: this runs
    the CLI with our environment, and the provider is kept there."""
    entry = runner.KNOWN["opencode"]
    binary = runner.resolve(entry["binary"])
    if binary is None:
        print(_("no agent: {binary} not found").format(binary=entry["binary"]),
              file=sys.stderr)
        return 1
    env = runner.agent_env(runner.agent_home(),
                           runner.env("opencode", "login", "", "", ""))
    print(_("agent's home: {where}").format(where=runner.agent_home()))
    return subprocess.run([binary, "auth", "login"], env=env).returncode


def _store_key(name: str) -> int:
    """Keep a token for the agent on this machine, typed and never echoed."""
    if name not in keys.NAMES:
        print(_("no key {name}; known: {known}").format(name=name, known=", ".join(keys.NAMES)),
              file=sys.stderr)
        return 1
    value = getpass.getpass(_("{name} (not shown): ").format(name=name)).strip()
    if not value:
        print(_("nothing entered — not saved"), file=sys.stderr)
        return 1
    keys.store(name, value)
    print(_("{name} is saved on {host}. The hands take it when they start again: "
            "after any change in ⚙ or a restart.").format(name=name, host=socket.gethostname()))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="heare.cli_hands")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")
    key = sub.add_parser("key", help=_("save the agent's token on this machine"))
    key.add_argument("name", choices=keys.NAMES)
    sub.add_parser("login", help=_("sign the agent in to its own home"))
    args = parser.parse_args()
    if args.command == "key":
        return _store_key(args.name)
    if args.command == "login":
        return _agent_login()
    logs.setup(args.verbose)
    layout.settle()
    try:
        while (code := asyncio.run(run())) == RESTART:
            logs.say(_("hands: new settings, starting again"))
        return code
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
