"""The native hands: a terminal on this Mac, and the bodies, with no agent.

    uv run python -m heare.native_hands

The cli hands take seconds to start and a long prompt to read; turning on a
light needs neither. These hands are a part like the others: they knock as an
executor, claim work, and do it with the voice's model — one command in the
shell, or one skill of one part, in a few calls at most. Whatever needs a
plan, files, code or questions belongs to the cli hands.

Two tools, and nothing about kinds of device:

* ``run`` — a command in ``/bin/sh`` on this machine. Whatever a command can
  do (install an app, open it, look, change a setting) is done by it, and the
  person's mode says who agrees. It is not a sandbox: after a yes the command
  has the owner's rights, and a short list of things is never run.
* ``body`` — one skill of one part, with the pid that offers it and the
  skill's own arguments. ``read`` and ``harmless`` are done at once;
  ``change`` goes to the judge. The same door is ``heare-bodies`` on the
  command line, for the cli hands and for a person.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import re
import signal
import sys
import time
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable

import httpx

from heare import bodies
from heare import hand as hand_life
from heare import layout, logs, part, talk
from heare.config import Settings
from heare.core.forms import Model
from heare.core.i18n import N_

logger = logging.getLogger("heare.native_hands")

CHANNEL = "native-hands"
# Which bodies are here and what they can do the voice reads from the
# registry itself; this says only what these hands are for.
ABOUT = (
    "acts on the bodies in «My body»; runs commands on its own machine — install "
    "an app, open one, look. Quick acts take seconds; anything long goes to the "
    "cli hands."
)
STEPS = 8
DEADLINE_S = 900.0   # the whole loop's ceiling: a yes from the person is in it
CALL_S = 620.0       # one act, the wait for that yes included
SKILLS_S = 5.0       # the brain has to say what the parts can do
HANDOFF = "pass_to_cli_hands"
BODY = "body"
SHOW = "show"
SHOW_CHARS = 200_000
SHOW_BYTES = 4_000_000
SHOW_S = 20.0
# What one task may send the model, in characters: a ceiling on the bill that
# holds whichever model is chosen. About a million tokens.
MAX_SENT = 4_000_000
KEPT_RESULTS = 8     # the last results stay whole; older ones are cut
CUT_CHARS = 400
# The terminal: one command, the owner's rights, the mode's leave.
RUN = "run"
RUN_S = 300.0
RUN_MAX_S = 600.0
OUTPUT_CHARS = 8000
READ_BYTES = OUTPUT_CHARS * 4
WATCH_S = 2.0        # how often a running command asks whether it is still wanted
# What a command's shell is given: a terminal's world, without the keys a
# service may carry in its own environment.
KEEP_ENV = ("HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "TERM",
            "SSH_AUTH_SOCK")
SHELL_PATH = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
              "/usr/sbin", "/sbin")
# Never, whatever the mode: sudo hangs without a TTY, and the rest take the
# machine over or hand out what is not the work's.
NEVER = ("sudo", "sudoedit", "doas", "security", "launchctl", "csrutil")
NEVER_SAID = ("spctl --master-disable", "diskutil erase", ".heare/machine",
              ".heare/memory", "Library/Keychains", ".ssh", ".aws", ".gnupg",
              ".netrc", "id_rsa", "id_ed25519")
ERASE_ROOT = re.compile(r"(?:^|[;&|]\s*)rm\s+(?:-[^\s]+\s+)*/(?:\s|$|\*)")
# A line of the reason that looks like a key is masked before the person
# hears it: an exception's text is written by a machine, not chosen.
LOOSE_SECRET = re.compile(
    r"sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}"
    r"|bearer\s+[A-Za-z0-9._~+/-]{16,}",
    re.IGNORECASE)

SAME_CALL = (
    "You already called this with these arguments; this is that answer. "
    "Use it, call something else, or answer."
)
FORCE_ANSWER = (
    "You have done enough for now. Answer the person in one or two sentences "
    "with what came of it. Call no tool."
)

_SYSTEM = (
    "You are heare's quick hands. Do the request with the tools and answer in "
    "one short sentence, in the language of the request: what was done. Do not "
    "make up what the tools did not say. If nothing here can do it, say so at "
    "once and why: do not look around, do not try another way.\n\n"
    "run is a shell on this Mac: install an app, open one, look, change a "
    "setting. The bodies are the `body` tool: one part, one skill, its own "
    "arguments, named as the list under it shows. Never a body through a "
    "command. When you made something to look at — a page, a picture — call "
    "`show` with it: the person sees it in the chat.\n\n"
    "If the request needs a plan, several attempts, files or code, do not do it "
    f"yourself — call {HANDOFF} with the task, whole and clear without the talk."
)


class NativeHand:
    about = ABOUT

    def __init__(self, model: Model, devices: bodies.Devices,
                 settings: Settings | None = None,
                 show: Callable[[int, dict], Awaitable[dict]] | None = None) -> None:
        self._model = model
        self._devices = devices
        self._settings = settings
        self._show_call = show

    async def run(self, task: str, desk: hand_life.Desk) -> str:
        try:
            skilled = await asyncio.wait_for(self._devices.skilled(), SKILLS_S)
        except TimeoutError:
            skilled = {}
        schemas = [_body(skilled), _RUN, _SHOW, _HANDOFF]
        messages: list[dict] = [{"role": "system", "content": _SYSTEM},
                                {"role": "user", "content": task}]
        done: list[str] = []
        seen: dict[str, str] = {}
        repeats = 0
        started = time.monotonic()
        taken = sent = 0
        while taken < STEPS and sent < MAX_SENT:
            taken += 1
            if desk.stopped:
                raise asyncio.CancelledError
            left = DEADLINE_S - (time.monotonic() - started)
            if left <= 0:
                break
            desk.step(N_("thinking"))
            try:
                text, calls = await asyncio.wait_for(
                    self._turn(_cut(messages), schemas), left)
                sent += sum(len(str(m.get("content"))) for m in _cut(messages))
            except TimeoutError:
                break
            if desk.stopped:
                raise asyncio.CancelledError
            if not calls:
                return text or "Done."
            for call in calls:
                if call.get("name") == HANDOFF:
                    raise hand_life.Handoff(_handed(call) or task)
            results: list[tuple[dict, str]] = []
            for call in calls:
                if desk.stopped:
                    raise asyncio.CancelledError
                # The deadline holds between calls too: a turn with several of
                # them must not outlive the loop's ceiling.
                left = DEADLINE_S - (time.monotonic() - started)
                if left <= 0:
                    break
                key = json.dumps(
                    [call.get("name"), call.get("arguments")],
                    sort_keys=True, default=str,
                )
                if key in seen:
                    repeats += 1
                    results.append((call, f"{SAME_CALL}\n{seen[key]}"))
                    continue
                wait = min(CALL_S, left)
                try:
                    said = await asyncio.wait_for(
                        self._call(skilled, call, desk), wait)
                except TimeoutError:
                    said = (f"Failed: {call.get('name')} did not answer in "
                            f"{int(wait)} s; whether it happened is unknown.")
                seen[key] = said
                results.append((call, said))
            done += [said for _, said in results]
            messages += _turns(text, results)
            if repeats >= 2:
                break
        if desk.stopped:
            raise asyncio.CancelledError
        # Out of turns or time: the model says what came of it in words, but
        # only with what is left of the same budget.
        left = DEADLINE_S - (time.monotonic() - started)
        reply = ""
        if left > 0:
            try:
                reply, _calls = await asyncio.wait_for(
                    self._turn([*messages, {"role": "user", "content": FORCE_ANSWER}], []),
                    left)
            except TimeoutError:
                reply = ""
        if reply:
            return reply
        last = next((said for said in reversed(done) if said.strip()), "")
        if not last:
            return "Done."
        lines = [line.strip() for line in last.splitlines() if line.strip()]
        # The command and its code are the shell's frame, not what came of it.
        useful = [line for line in lines if not line.startswith(("$ ", "exit "))]
        return f"Done: {(useful or lines)[0][:200]}"

    async def _turn(self, messages: list[dict], tools: list[dict]) -> tuple[str, list[dict]]:
        parts: list[str] = []
        calls: list[dict] = []
        async for event in self._model.reply(messages, tools):
            if event.get("type") == "delta":
                parts.append(event.get("text", ""))
            elif event.get("type") == "tool_call":
                calls.append(event)
        return "".join(parts).strip(), calls

    async def _call(self, skilled: dict[str, dict], call: dict,
                    desk: hand_life.Desk) -> str:
        args = call.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except ValueError:
                args = {}
        if not isinstance(args, dict):
            return "No such tool."
        name = str(call.get("name") or "")
        if name == RUN:
            return await self._run(args, desk)
        if name == SHOW:
            return await self._show(args, desk)
        if name == BODY:
            return await self._body(skilled, args, desk)
        return "No such tool."

    async def _body(self, skilled: dict[str, dict], args: dict,
                    desk: hand_life.Desk) -> str:
        pid = str(args.get("part") or "").strip()
        skill_name = str(args.get("skill") or "").strip()
        skill = bodies.find(skilled, pid, skill_name)
        if skill is None:
            return (f"No such skill: {pid} {skill_name}. " + bodies.describe(skilled))
        given = args.get("args")
        if isinstance(given, str):
            try:
                given = json.loads(given or "{}")
            except ValueError:
                given = {}
        if not isinstance(given, dict):
            given = {}
        desk.step(f"{pid} {skill_name}")
        return await bodies.call(self._devices, pid, skill, given, desk.permit)

    async def _show(self, args: dict, desk: hand_life.Desk) -> str:
        """Something to look at, in the chat: a card, a picture, a link. The
        brain draws it; these hands only say what and send the bytes."""
        if self._show_call is None:
            return "Failed: these hands cannot show anything here."
        kind = str(args.get("kind") or "").strip()
        body: dict = {"kind": kind, "title": str(args.get("title") or ""),
                      "say": str(args.get("say") or ""),
                      "width": str(args.get("width") or "")}
        if kind == "html":
            html = str(args.get("html") or "")
            path = str(args.get("file") or "").strip()
            if not html and path:
                try:
                    html = _read_text(path, SHOW_CHARS)
                except OSError as exc:
                    return f"Failed: {exc}"
            if not html.strip():
                return "Failed: no html and no file."
            body["html"] = html[:SHOW_CHARS]
        elif kind == "link":
            body["url"] = str(args.get("url") or "").strip()
        elif kind in ("image", "audio", "video"):
            path = str(args.get("file") or "").strip()
            if not path:
                return "Failed: no file."
            try:
                raw = _read_bytes(path, SHOW_BYTES)
            except (OSError, ValueError) as exc:
                return f"Failed: {exc}"
            body["data"] = base64.b64encode(raw).decode()
            body["name"] = Path(path).name
        else:
            return "Failed: no such kind."
        try:
            answer = await self._show_call(desk.job_id, body)
        except Exception as exc:  # noqa: BLE001 — the model is told, not crashed
            return f"Failed: {exc}"
        return f"Shown: {answer.get('shown') or kind}."

    # -- the terminal ----------------------------------------------------

    def _mode(self) -> str:
        """Who agrees to a command: the person, the judge, or nobody."""
        said = str(getattr(self._settings, "executor_mode", "") or "").strip()
        return said if said in ("careful", "normal", "auto") else "normal"

    async def _allowed(self, command: str, cwd: Path, desk: hand_life.Desk) -> bool:
        mode = self._mode()
        if mode == "auto":
            return True
        kind = "change" if mode == "careful" else "judge"
        return await desk.permit(command, kind, f"{command}\n(cwd: {cwd})")

    async def _run(self, args: dict, desk: hand_life.Desk) -> str:
        command = str(args.get("command") or "").strip()
        if not command:
            return "Failed: no command."
        banned = refused(command)
        if banned:
            return f"Not done: {banned} is never run by the hands."
        cwd = _folder(args.get("cwd"))
        if cwd is None:
            return f"Failed: no such folder: {str(args.get('cwd'))[:200]}"
        if not await self._allowed(command, cwd, desk):
            return "Not done: no permission."
        if desk.stopped:
            raise asyncio.CancelledError
        return await self._shell(command, cwd, _limit(args.get("timeout")), desk)

    async def _shell(self, command: str, cwd: Path, limit: float,
                     desk: hand_life.Desk) -> str:
        desk.step(f"$ {command[:70]}")
        try:
            process = await asyncio.create_subprocess_shell(
                command, cwd=str(cwd), stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True, env=shell_env())
        except OSError as exc:
            return f"Failed: {exc}"
        stopped = {"yes": False}
        watcher = asyncio.create_task(self._watch(process, desk, stopped))
        try:
            try:
                said, _cut_out = await asyncio.wait_for(_finish(process), limit)
            except TimeoutError:
                await _kill(process)
                return f"Failed: no end in {int(limit)} s; the command was stopped."
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            if process.returncode is None:
                await _kill(process)
        if stopped["yes"]:
            raise asyncio.CancelledError
        text = _capped(said.decode("utf-8", "replace"))
        tail = f"\n{text}" if text else ""
        return f"$ {command}\nexit {process.returncode}{tail}"

    async def _watch(self, process: asyncio.subprocess.Process,
                     desk: hand_life.Desk, stopped: dict[str, bool]) -> None:
        """While a command runs, ask the brain whether it is still wanted:
        a step is the only way the person's «стоп» reaches a hand."""
        while process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), WATCH_S)
                return
            except TimeoutError:
                pass
            if desk.stopped:
                stopped["yes"] = True
                await _kill(process)
                return
            desk.step(N_("thinking"))


def _body(skilled: dict[str, dict]) -> dict:
    """The one tool that reaches a part, with the list it names it by."""
    return {"type": "function", "function": {
        "name": BODY,
        "description": "Act on one body: name its pid, the skill and the skill's "
                       "arguments. The bodies and their skills:\n" + bodies.describe(skilled),
        "parameters": {"type": "object", "required": ["part", "skill"], "properties": {
            "part": {"type": "string", "description": "The body's pid, as listed."},
            "skill": {"type": "string", "description": "The skill's name."},
            "args": {"type": "object", "description": "The skill's arguments."}}}}}


_RUN = {"type": "function", "function": {
    "name": RUN,
    "description": "Run one command on this Mac with /bin/sh: install an app "
                   "(brew install --cask …), open one (open -a …), look (ls, ps, "
                   "defaults read), change a setting. Pipes, && and redirects "
                   "work. The person's mode decides who agrees. The bodies are "
                   "their own tool; use that, not a command.",
    "parameters": {"type": "object", "required": ["command"], "properties": {
        "command": {"type": "string", "description": "The command line."},
        "cwd": {"type": "string", "description": "The folder to run in; ~ is home."},
        "timeout": {"type": "integer", "description": "Seconds to wait: 1..600, "
                                                     "300 unless given."}}}}}

_SHOW = {"type": "function", "function": {
    "name": SHOW,
    "description": "Put something in the chat for the person: a page or a picture "
                   "you made (html, or a .html file on this machine), an image, "
                   "audio or video file, or a link. Call it as soon as there is "
                   "something to look at — the card appears while you finish.",
    "parameters": {"type": "object", "required": ["kind"], "properties": {
        "kind": {"type": "string", "enum": ["html", "image", "audio", "video", "link"]},
        "html": {"type": "string", "description": "The card's HTML."},
        "file": {"type": "string", "description": "A file on this machine: .html "
                                                  "for a page, an image, audio or video."},
        "url": {"type": "string", "description": "An http(s) link."},
        "title": {"type": "string", "description": "A short title for the card."},
        "say": {"type": "string", "description": "One short sentence to say."},
        "width": {"type": "string", "enum": ["s", "w"]}}}}}


def _read_text(path: str, cap: int) -> str:
    found = Path(path).expanduser()
    if not found.is_file():
        raise OSError("no such file")
    return found.read_text(encoding="utf-8", errors="replace")[:cap]


def _read_bytes(path: str, cap: int) -> bytes:
    found = Path(path).expanduser()
    if not found.is_file():
        raise OSError("no such file")
    if found.stat().st_size > cap:
        raise ValueError(f"the file is larger than {cap // 1_000_000} MB")
    return found.read_bytes()


def _folder(raw: object) -> Path | None:
    if not raw:
        return Path.home()
    path = Path(str(raw)).expanduser()
    return path if path.is_dir() else None


def _limit(raw: object) -> float:
    try:
        said = float(raw)  # type: ignore[arg-type] — the model's answer is untrusted
    except (TypeError, ValueError):
        return RUN_S
    return min(max(said, 1.0), RUN_MAX_S)


def _capped(text: str) -> str:
    if len(text) <= OUTPUT_CHARS:
        return text
    return text[:OUTPUT_CHARS] + f"\n… cut, {len(text)} characters in all"


def shell_env() -> dict[str, str]:
    """A shell's world, without the keys a service may carry in its own."""
    env = {name: os.environ[name] for name in KEEP_ENV if name in os.environ}
    env.setdefault("HOME", str(Path.home()))
    home = Path.home() / ".local/bin"
    env["PATH"] = ":".join([SHELL_PATH[0], SHELL_PATH[1], str(home), *SHELL_PATH[2:]])
    return env


def refused(command: str) -> str:
    """The first word or phrase that makes a command one the hands never run.

    Matched on the command as written: a shell can go round a list, so this is
    for the honest mistake, not a wall."""
    words = set(re.findall(r"[\w.-]+", command))
    for word in NEVER:
        if word in words:
            return word
    for phrase in NEVER_SAID:
        if phrase in command:
            return phrase
    if ERASE_ROOT.search(command):
        return "rm -rf /"
    return ""


async def _finish(process: asyncio.subprocess.Process) -> tuple[bytes, bool]:
    """The command's output, to a cap, and whether it was cut."""
    assert process.stdout is not None
    data = bytearray()
    while True:
        chunk = await process.stdout.read(8192)
        if not chunk:
            await process.wait()
            return bytes(data), False
        data += chunk
        if len(data) >= READ_BYTES:
            await _kill(process)
            return bytes(data), True


async def _kill(process: asyncio.subprocess.Process) -> None:
    """The whole group, not the shell: a command's children are the command."""
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            process.kill()
        except ProcessLookupError:
            pass
    try:
        await process.wait()
    except ProcessLookupError:
        pass


def _handed(call: dict) -> str:
    args = call.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except ValueError:
            return ""
    return str(args.get("task") or "").strip() if isinstance(args, dict) else ""


def _cut(messages: list[dict]) -> list[dict]:
    """Old results shortened, so long work does not fill the model's memory."""
    told = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    old = set(told[:-KEPT_RESULTS])
    return [
        {**m, "content": str(m.get("content"))[:CUT_CHARS] + " …cut"}
        if i in old and len(str(m.get("content"))) > CUT_CHARS else m
        for i, m in enumerate(messages)]


_HANDOFF = {"type": "function", "function": {
    "name": HANDOFF,
    "description": "Not yours: give the work to the cli hands — an agent with files, "
                   "code, the shell and the internet.",
    "parameters": {"type": "object", "required": ["task"], "properties": {
        "task": {"type": "string", "description": "The whole task, clear without the talk."}}}}}


def _turns(text: str, results: list[tuple[dict, str]]) -> list[dict]:
    """The calls and what they came to, in the protocol's own turns."""
    ids = [call.get("id") or f"call_{index}" for index, (call, _said) in enumerate(results, 1)]
    calls = []
    for call_id, (call, _said) in zip(ids, results):
        arguments = call.get("arguments") or {}
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        calls.append({"id": call_id, "type": "function",
                      "function": {"name": call.get("name", ""), "arguments": arguments}})
    turns: list[dict] = [{"role": "assistant", "content": text, "tool_calls": calls}]
    for call_id, (_call, said) in zip(ids, results):
        turns.append({"role": "tool", "tool_call_id": call_id, "content": said})
    return turns


def _reason(exc: Exception) -> str:
    """What went wrong, in the one line the person may hear; the log has all.

    The model's own failures are said by class, not by the exception's text: a
    provider's message carries the request, and the request carries the key."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = getattr(exc.response, "status_code", "?")
        return f"Failed: the model refused ({code})."
    if isinstance(exc, httpx.TimeoutException):
        return "Failed: the model did not answer in time."
    said = LOOSE_SECRET.sub("[secret]", " ".join(str(exc).split()))[:200]
    return f"Failed: {type(exc).__name__}: {said}" if said else f"Failed: {type(exc).__name__}."


def chosen(settings: Settings) -> talk.Wire:
    """The model the voice talks with: the same provider, model and key.

    The provider asked for first; if it has no key, the first that has one —
    the same order the brain falls back in."""
    from heare.model.providers import PROVIDERS

    wanted = str(settings.llm_provider or "deepseek").lower()
    for name in [wanted] + [k for k in PROVIDERS if k != wanted]:
        cfg = PROVIDERS.get(name)
        key = getattr(settings, cfg.api_key_attr, None) if cfg else None
        if cfg is None or not key or cfg.api_style not in ("openai", "anthropic"):
            continue
        return talk.Wire(
            base_url=getattr(settings, f"{name}_base_url", "") or cfg.base_url,
            key=key,
            model=getattr(settings, f"{name}_model", "") or cfg.default_model,
            style=cfg.api_style)
    raise RuntimeError("no key for any model")


class Chosen:
    """The voice's model, chosen afresh each turn: a key or a provider
    changed on the page is used without a restart."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings, self._client = settings, client

    async def reply(self, messages: list[dict], verbs: list[dict]) -> AsyncIterator[dict]:
        async for event in talk.Talk(chosen(self._settings), self._client).reply(messages, verbs):
            yield event


async def run(settings: Settings | None = None, model: Model | None = None,
              host: str | None = None) -> int:
    """The part's life; a bench hands in its own brain, model and host."""
    settings = settings or Settings()
    host = host or settings.host
    # No ceiling on the client: a permission waits for the person, and a
    # socket timeout must not turn their thinking into a lost yes. The calls
    # that can hang — a model, a skill — set their own.
    async with httpx.AsyncClient(timeout=None) as client:

        async def work(wire: part.Wire) -> None:
            async def show(job: int, body: dict) -> dict:
                answer = await wire.post(f"/api/executor/{job}/show", body,
                                         timeout=SHOW_S)
                if answer.status_code != 200:
                    raise RuntimeError(answer.text[:200] or "the page did not take it")
                return answer.json()

            hand = NativeHand(model or Chosen(settings, client), bodies.Parts(wire),
                              settings, show=show)
            wire.report({"work": N_("waiting")})

            async def do(job: hand_life.Job, desk: hand_life.Desk) -> str:
                return await hand.run(job.request, desk)

            await hand_life.serve(
                wire, do,
                on_begin=lambda job, desk: wire.report({"work": f"working: {job.request[:50]}"}),
                on_end=lambda job, status, result: wire.report({"work": N_("waiting")}),
                # The person hears a sentence; the reason goes to the log.
                failed=_reason)

        return await part.live(
            {"channel": CHANNEL, "forms": ["executor"], "about": ABOUT,
             "key": f"{CHANNEL}@{host}", "host": host,
             "keys": [f"{settings.llm_provider or 'deepseek'}_API_KEY".upper()],
             "package": "heare-native-hands",
             "ui": [{"name": "work", "kind": "label", "label": N_("Work"),
                     "value": N_("waiting"), "head": True}]},
            work, hello="native hands", settings=settings, client=client)


def main() -> int:
    parser = argparse.ArgumentParser(prog="heare.native_hands")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logs.setup(args.verbose)
    layout.settle()
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
