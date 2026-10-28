"""The native hands: quick, simple acts on the bodies, with no agent and no session.

    uv run python -m heare.native_hands

The cli hands take seconds to start and a long prompt to read; turning on a
light needs neither. These hands are a part like the others: they knock as an
executor, claim work, and do it with the voice's model and the other parts'
skills as tools, in a few calls at most. Whatever needs a plan, files, code
or questions belongs to the cli hands.

The hands know no kinds of device. A skill comes from its part with its name,
its words, its parameters and its effect, and is a tool as it is
(``part__skill``). ``read`` and ``harmless`` are done at once; ``change`` goes
through the desk's ``judge``: what the person plainly asked for is done,
anything else is asked. Two parts of one name share a tool, and the person is
asked which.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import socket
import sys
import time
from dataclasses import dataclass
from typing import AsyncIterator, Protocol

import httpx

from heare import hand as hand_life
from heare import layout, logs, part, pixels, talk
from heare.config import Settings
from heare.core.forms import Model
from heare.core.i18n import N_, _

logger = logging.getLogger("heare.native_hands")

CHANNEL = "native-hands"
# Which bodies are here and what they can do the voice reads from the
# registry itself; this says only what these hands are for.
ABOUT = (
    "quick acts on the bodies in «My body»: show, switch, read. One or two "
    "acts, no plan, no files, code, shell or internet."
)
STEPS = 4            # 1 turn to understand, 1–2 to act, 1 to answer
DEADLINE_S = 15.0    # the whole loop's ceiling; checked before every turn
CALL_S = 10.0        # one skill; a hung part is a step, not forever
HANDOFF = "pass_to_cli_hands"
FAILED = "Failed: the model did not answer."

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
    "make up what the tools did not say. If the request is not for these tools "
    "(files, code, shell, internet, a plan, anything long), do not answer "
    f"yourself — call {HANDOFF} with the task, whole and clear without the talk."
)


class Devices(Protocol):
    """The other parts' skills, as the brain tells them."""

    async def skilled(self) -> dict[str, dict]:
        """By pid: the part's name, about, channel and skills."""
        ...

    async def call(self, pid: str, name: str, args: dict) -> dict:
        """One skill called; the part's answer as ``{"ok", "result"}``."""
        ...


@dataclass(frozen=True)
class Tool:
    """A skill as the model sees it, and every part that offers it."""

    name: str
    skill: dict
    parts: tuple[tuple[str, dict], ...]  # (pid, the part's entry)

    def schema(self) -> dict:
        _pid, entry = self.parts[0]
        where = f"{entry['name']} ({entry['about']})" if entry.get("about") else entry["name"]
        return {"type": "function", "function": {
            "name": self.name,
            "description": f"{where}: {self.skill['about']}",
            "parameters": self.skill["params"]}}


class NativeHand:
    about = ABOUT

    def __init__(self, model: Model, devices: Devices) -> None:
        self._model = model
        self._devices = devices

    async def run(self, task: str, desk: hand_life.Desk) -> str:
        tools = catalog(await self._devices.skilled())
        schemas = [tool.schema() for tool in tools.values()] + [_HANDOFF]
        messages: list[dict] = [{"role": "system", "content": _SYSTEM},
                                {"role": "user", "content": task}]
        done: list[str] = []
        seen: dict[str, str] = {}
        repeats = 0
        started = time.monotonic()
        for _step in range(STEPS):
            if desk.stopped:
                raise asyncio.CancelledError
            if time.monotonic() - started >= DEADLINE_S:
                break
            desk.step(N_("thinking"))
            text, calls = await self._turn(messages, schemas)
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
                key = json.dumps(
                    [call.get("name"), call.get("arguments")],
                    sort_keys=True, default=str,
                )
                if key in seen:
                    repeats += 1
                    results.append((call, f"{SAME_CALL}\n{seen[key]}"))
                    continue
                try:
                    said = await asyncio.wait_for(
                        self._call(tools, call, desk), CALL_S)
                except TimeoutError:
                    said = (f"Failed: {call.get('name')} did not answer in "
                            f"{int(CALL_S)} s; whether it happened is unknown.")
                seen[key] = said
                results.append((call, said))
            done += [said for _, said in results]
            messages += _turns(text, results)
            if repeats >= 2:
                break
        # Out of turns or time: the model says what came of it in words.
        reply, _calls = await self._turn(
            [*messages, {"role": "user", "content": FORCE_ANSWER}], [])
        return reply or ("Done: " + " ".join(done[-3:]))

    async def _turn(self, messages: list[dict], tools: list[dict]) -> tuple[str, list[dict]]:
        parts: list[str] = []
        calls: list[dict] = []
        async for event in self._model.reply(messages, tools):
            if event.get("type") == "delta":
                parts.append(event.get("text", ""))
            elif event.get("type") == "tool_call":
                calls.append(event)
        return "".join(parts).strip(), calls

    async def _call(self, tools: dict[str, Tool], call: dict, desk: hand_life.Desk) -> str:
        args = call.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except ValueError:
                args = {}
        tool = tools.get(str(call.get("name")))
        if tool is None or not isinstance(args, dict):
            return "No such skill."
        pid = await _which(tool, desk)
        if pid is None:
            return "Not clear which part."
        desk.step(tool.name)
        if tool.skill["effect"] == "change":
            what = f"{tool.skill['about']} — {tool.name} {json.dumps(args, ensure_ascii=False)}"
            if not await desk.permit(what, "judge", what):
                return "Not done: no permission."
        try:
            args = _rendered(tool.skill, args)
        except pixels.Unfit as unfit:
            # Said to the model, which may draw it again within its steps.
            return f"Failed: {unfit}"
        answer = await self._devices.call(pid, tool.skill["name"], args)
        result = answer.get("result")
        said = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        return said if answer.get("ok") else f"Failed: {said}"


_HANDOFF = {"type": "function", "function": {
    "name": HANDOFF,
    "description": "Not yours: give the work to the cli hands — an agent with files, "
                   "code, the shell and the internet.",
    "parameters": {"type": "object", "required": ["task"], "properties": {
        "task": {"type": "string", "description": "The whole task, clear without the talk."}}}}}


def _handed(call: dict) -> str:
    args = call.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except ValueError:
            return ""
    return str(args.get("task") or "").strip() if isinstance(args, dict) else ""


def _rendered(skill: dict, args: dict) -> dict:
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


def catalog(skilled: dict[str, dict]) -> dict[str, Tool]:
    """Every part's skills as tools, named ``part__skill``; parts of one name
    (two boards both called «desk») share a tool and are asked about."""
    found: dict[str, Tool] = {}
    for pid, entry in skilled.items():
        for skill in entry["skills"]:
            name = _tool_name(f"{entry['name']}__{skill['name']}")
            had = found.get(name)
            parts = (had.parts if had else ()) + ((pid, entry),)
            found[name] = Tool(name, had.skill if had else skill, parts)
    return found


async def _which(tool: Tool, desk: hand_life.Desk) -> str | None:
    """The part a call is for; the person is asked when two answer to one name."""
    if len(tool.parts) == 1:
        return tool.parts[0][0]
    listed = "; ".join(f"{n}) {pid}" for n, (pid, _entry) in enumerate(tool.parts, 1))
    answer = (await desk.ask(_("Several parts answer to that: {parts}. Which?").format(
        parts=listed))).strip()
    for n, (pid, _entry) in enumerate(tool.parts, 1):
        if str(n) in re.findall(r"\d+", answer) or pid in answer:
            return pid
    return None


def _tool_name(text: str) -> str:
    """A name the model's tools accept: letters, digits, _ and -, 64 at most."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", text)[:64]


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


class Parts:
    """The other parts' skills, over the door."""

    def __init__(self, wire: part.Wire) -> None:
        self._wire = wire

    async def skilled(self) -> dict[str, dict]:
        try:
            answer = await self._wire.client.get(f"{self._wire.base}/api/skills")
        except httpx.HTTPError:
            return {}
        return answer.json() if answer.status_code == 200 else {}

    async def call(self, pid: str, name: str, args: dict) -> dict:
        answer = await self._wire.post("/api/skills/call",
                                       {"part": pid, "name": name, "args": args})
        if answer.status_code != 200:
            return {"ok": False, "result": "the part has no such skill"}
        return answer.json()


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
    host = host or socket.gethostname()
    async with httpx.AsyncClient(timeout=None) as client:

        async def work(wire: part.Wire) -> None:
            hand = NativeHand(model or Chosen(settings, client), Parts(wire))
            wire.report({"work": N_("waiting")})

            async def do(job: hand_life.Job, desk: hand_life.Desk) -> str:
                return await hand.run(job.request, desk)

            await hand_life.serve(
                wire, CHANNEL, do,
                on_begin=lambda job, desk: wire.report({"work": f"working: {job.request[:50]}"}),
                on_end=lambda job, status, result: wire.report({"work": N_("waiting")}),
                # The person hears a sentence; the reason goes to the log.
                failed=lambda exc: FAILED)

        return await part.live(
            {"channel": CHANNEL, "forms": ["executor"], "about": ABOUT,
             "key": f"{CHANNEL}@{host}", "host": host,
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
