"""The bodies: what the parts can do, listed and called.

One implementation for everyone who acts on a part — the native hands' ``body``
tool and the ``heare-bodies`` command an agent or a person runs. A skill is
called as its part declared it: its arguments rendered first by its ``render``
rule, and a ``change`` skill asks the judge before it is done.

The pid says which part: two boards of one name are told apart by the caller,
not by a question. The list is what the brain knows, nothing is cached here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Awaitable, Callable, Protocol

import httpx

from heare import part, pixels
from heare.config import Settings

# The task a command runs inside: the hands put it here, so a body's `change`
# asks the judge with the words of the work it belongs to.
JOB = "HEARE_JOB"
SKILLS_S = 5.0
CALL_S = 35.0        # a skill's answer: its `within` is 30 at most
DESCRIBE_CHARS = 4000
ABOUT_CHARS = 70


class Devices(Protocol):
    """The parts' skills, as the brain tells them."""

    async def skilled(self) -> dict[str, dict]:
        """By pid: the part's name, about, channel and skills."""
        ...

    async def call(self, pid: str, name: str, args: dict) -> dict:
        """One skill called; the part's answer as ``{"ok", "result"}``."""
        ...


Permit = Callable[[str, str, str], Awaitable[bool]]


class Parts:
    """The parts' skills, over the door."""

    def __init__(self, wire: part.Wire) -> None:
        self._wire = wire

    async def skilled(self) -> dict[str, dict]:
        try:
            answer = await self._wire.client.get(f"{self._wire.base}/api/skills",
                                                 timeout=SKILLS_S)
        except httpx.HTTPError:
            return {}
        return answer.json() if answer.status_code == 200 else {}

    async def call(self, pid: str, name: str, args: dict) -> dict:
        answer = await self._wire.post("/api/skills/call",
                                       {"part": pid, "name": name, "args": args},
                                       timeout=CALL_S)
        if answer.status_code != 200:
            return {"ok": False, "result": "the part has no such skill"}
        return answer.json()


def describe(skilled: dict[str, dict]) -> str:
    """The list a caller reads: every pid, and what it can do, in a few words."""
    lines: list[str] = []
    for pid, entry in skilled.items():
        found = "; ".join(_short(skill) for skill in entry["skills"])
        about = " ".join(str(entry.get("about") or "").split())[:60]
        where = f" — {about}" if about else ""
        lines.append(f"{pid} ({entry['name']}{where}): {found}")
    return "\n".join(lines)[:DESCRIBE_CHARS] or "no bodies are here"


def _short(skill: dict) -> str:
    params = ", ".join((skill.get("params") or {}).get("properties") or {})
    about = " ".join(str(skill.get("about") or "").split())[:ABOUT_CHARS]
    return f"{skill['name']}({params}) [{skill['effect']}] — {about}"


def find(skilled: dict[str, dict], pid: str, name: str) -> dict | None:
    """The skill a call names; no part, no skill, nothing to ask about."""
    entry = skilled.get(pid)
    if entry is None:
        return None
    return next((skill for skill in entry["skills"] if skill["name"] == name), None)


async def call(devices: Devices, pid: str, skill: dict, args: dict, permit: Permit) -> str:
    """One skill, its leave taken, its arguments as the part takes them."""
    if skill["effect"] == "change":
        what = f"{skill['about']} — {skill['name']} {json.dumps(args, ensure_ascii=False)}"
        if not await permit(what, "judge", what):
            return "Not done: no permission."
    try:
        args = rendered(skill, args)
    except pixels.Unfit as unfit:
        # Said to the model, which may draw it again within its steps.
        return f"Failed: {unfit}"
    answer = await devices.call(pid, skill["name"], args)
    result = answer.get("result")
    said = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    return said if answer.get("ok") else f"Failed: {said}"


def rendered(skill: dict, args: dict) -> dict:
    """The arguments as the part takes them: what it asked rendered, rendered.

    A rule names the parameter itself, or — ``deep`` — a key to find inside
    it, in objects that match ``where`` (an icon's SVG at 16×16, a picture's
    at the window's size)."""
    found = json.loads(json.dumps(args))
    for name, spec in (skill.get("render") or {}).items():
        if name not in found:
            continue
        if isinstance(spec, list):
            for rule in spec:
                _deep(found[name], rule)
        else:
            found[spec.get("as") or name] = pixels.render(str(found.pop(name)), spec)
    return found


def _deep(value: object, rule: dict) -> None:
    if isinstance(value, list):
        for item in value:
            _deep(item, rule)
        return
    if not isinstance(value, dict):
        return
    where = rule.get("where") or {}
    if rule["key"] in value and all(value.get(k) == v for k, v in where.items()):
        found = value.pop(rule["key"])
        if isinstance(found, list):
            # Frames: every SVG drawn alike, in order.
            value[rule["as"]] = [pixels.render(str(frame), rule) for frame in found]
            return
        value[rule["as"]] = pixels.render(str(found), rule)
    for item in list(value.values()):
        _deep(item, rule)


class JobPermit:
    """A body's leave while a task runs: the brain asks the judge or the person."""

    def __init__(self, wire: part.Wire, job: int) -> None:
        self._wire, self._job = wire, job

    async def __call__(self, what: str, kind: str, detail: str) -> bool:
        answer = await self._wire.post(
            f"/api/executor/{self._job}/permit",
            {"what": what, "kind": kind, "detail": detail})
        if answer.status_code >= 400:
            return False
        found = answer.json()
        return bool(found.get("allow")) and not found.get("stopped")


async def _never(what: str, kind: str, detail: str) -> bool:
    return False


def _say(ok: bool, text: str) -> int:
    print(f"ok: {text}" if ok else f"failed: {text}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    """``heare-bodies`` lists; ``heare-bodies call <pid> <skill> '<json>'`` calls.

    The list and the answers are English: a model reads them. A person runs the
    same command to see what the bodies are.
    """
    parser = argparse.ArgumentParser(prog="heare-bodies",
                                     description="List the bodies, or call one skill.")
    commands = parser.add_subparsers(dest="what")
    commands.add_parser("list", help="what the bodies can do (the default)")
    one = commands.add_parser("call", help="one skill of one part")
    one.add_argument("part", help="the body's pid, as `list` shows it")
    one.add_argument("skill", help="the skill's name")
    one.add_argument("args", nargs="?", default="{}", help="the arguments, as JSON")
    parsed = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        return asyncio.run(_work(parsed))
    except KeyboardInterrupt:
        return 0


async def _work(parsed: argparse.Namespace) -> int:
    settings = Settings()
    try:
        secret = settings.secret
    except OSError:
        return _say(False, "no key for this brain yet — start the brain first")
    async with httpx.AsyncClient(timeout=None) as client:
        client.headers["X-Heare-Token"] = secret
        wire = part.Wire(client, settings.base, "", "", secret)
        parts = Parts(wire)
        if parsed.what != "call":
            print(describe(await parts.skilled()))
            return 0
        return await _call(parts, wire, parsed)


async def _call(parts: Parts, wire: part.Wire, parsed: argparse.Namespace) -> int:
    skilled = await parts.skilled()
    skill = find(skilled, parsed.part, parsed.skill)
    if skill is None:
        return _say(False, f"no skill {parsed.part} {parsed.skill}")
    try:
        given = json.loads(parsed.args or "{}")
    except ValueError:
        return _say(False, "the arguments are not JSON")
    if not isinstance(given, dict):
        return _say(False, "the arguments are not an object")
    job = os.environ.get(JOB, "").strip()
    job = job if job.isdigit() else ""
    if skill["effect"] == "change" and not job:
        return _say(False, "a change needs a job — ask on the page")
    permit: Permit = JobPermit(wire, int(job)) if job else _never
    said = await call(parts, parsed.part, skill, given, permit)
    return _say(not said.startswith(("Failed:", "Not done:")), said)


if __name__ == "__main__":
    sys.exit(main())
