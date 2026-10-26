"""The native hand: quick, simple acts on the bodies, with no agent and no session.

The opencode hand takes seconds to start and a long prompt to read; turning on
a light needs neither. This hand is the voice's own model with the acts the
parts named (``acts`` in their knock) as its tools, in a few calls at most.
The dispatcher chooses between the hands by their ``about``; whatever needs a
plan, files, code or questions belongs to the other hand.

Showing and lighting change nothing that matters and are done at once. A pin
may be wired to anything, so it goes through the desk's ``judge``: what the
person plainly asked for is done, anything else is asked.
"""

from __future__ import annotations

import json
import logging
from typing import Callable, Protocol

from heare.core.forms import Desk, Model

logger = logging.getLogger("heare.native_hand")

NAME = "native hand"
ABOUT = (
    "швидкі прості дії з тілами heare: панель на столі — показати кілька "
    "слів, світло, увімкнути чи вимкнути пін, прочитати пін. Одна-дві дії, "
    "без плану, без файлів, коду, шелу й інтернету."
)
STEPS = 4
SHOW_S = 10
SHOW_MAX_S = 60
SHOW_CHARS = 40

_SYSTEM = (
    "Ти — швидкі руки heare. Зроби прохання інструментами й відповідай одним "
    "коротким реченням українською: що зроблено. Не вигадуй того, чого "
    "інструменти не сказали. Якщо прохання не про ці інструменти — скажи "
    "одним реченням, що це не твоє. На екран пиши латиницею: кирилиці він "
    "не має."
)


class Devices(Protocol):
    def acting(self) -> dict[str, dict]:
        """By pid: the part's channel, the acts it offers, its last values."""
        ...

    def act(self, pid: str, name: str, value: object) -> bool: ...


class NativeHand:
    name = NAME
    about = ABOUT

    def __init__(self, model: Model, devices: Callable[[], Devices | None]) -> None:
        self._model = model
        self._devices = devices

    async def run(self, task: str, desk: Desk) -> str:
        here = _found(self._devices())
        if here is None:
            return "Жодної частини з діями зараз немає."
        tools = _tools(here[1])
        messages: list[dict] = [{"role": "system", "content": _SYSTEM},
                                {"role": "user", "content": task}]
        for _ in range(STEPS):
            desk.step("думаю")
            text, calls = await self._turn(messages, tools)
            if not calls:
                return text or "Готово."
            results = [(call, await self._call(call, desk)) for call in calls]
            messages += _turns(text, results)
        return "Не впорався за кілька кроків."

    async def _turn(self, messages: list[dict], tools: list[dict]) -> tuple[str, list[dict]]:
        parts: list[str] = []
        calls: list[dict] = []
        async for event in self._model.reply(messages, tools):
            if event.get("type") == "delta":
                parts.append(event.get("text", ""))
            elif event.get("type") == "tool_call":
                calls.append(event)
        return "".join(parts).strip(), calls

    async def _call(self, call: dict, desk: Desk) -> str:
        arguments = call.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except ValueError:
                arguments = {}
        devices = self._devices()
        here = _found(devices)
        if devices is None or here is None:
            return "Частина пішла."
        pid, entry = here
        name = call.get("name")
        desk.step(str(name))
        if name == "show":
            return _show(devices, pid, entry, arguments)
        if name == "light":
            return _light(devices, pid, entry, arguments)
        if name == "switch":
            return await _switch(devices, pid, entry, arguments, desk)
        if name == "read_pin":
            return _read(entry, arguments)
        return "Такого інструмента немає."


def _found(devices: Devices | None) -> tuple[str, dict] | None:
    """The part to act through: the first, while there is one panel."""
    acting = devices.acting() if devices is not None else {}
    return next(iter(acting.items()), None)


def _named(entry: dict, kind: str) -> list[str]:
    return [a["name"] for a in entry["acts"] if a["kind"] == kind]


def _show(devices: Devices, pid: str, entry: dict, arguments: dict) -> str:
    text = str(arguments.get("text") or "").strip()[:SHOW_CHARS]
    if not _named(entry, "show") or not text:
        return "Нема що показати."
    try:
        seconds = int(arguments.get("seconds") or SHOW_S)
    except (TypeError, ValueError):
        seconds = SHOW_S
    seconds = max(1, min(seconds, SHOW_MAX_S))
    devices.act(pid, _named(entry, "show")[0], {"text": text, "seconds": seconds})
    return f"Показано на {seconds} с."


def _light(devices: Devices, pid: str, entry: dict, arguments: dict) -> str:
    mode = arguments.get("mode")
    if not _named(entry, "light") or mode not in ("on", "off", "blink"):
        return "Світло не зрозуміло, що робити."
    devices.act(pid, _named(entry, "light")[0], mode)
    return f"Світло: {mode}."


async def _switch(devices: Devices, pid: str, entry: dict, arguments: dict,
                  desk: Desk) -> str:
    pin = arguments.get("pin")
    if pin not in _named(entry, "switch"):
        return "Такого піна немає."
    on = arguments.get("on") is True
    what = f"{'увімкнути' if on else 'вимкнути'} {pin} на панелі"
    if not await desk.permit(what, "judge", what):
        return f"{pin} не чіпаю: дозволу немає."
    if not devices.act(pid, pin, on):
        return "Панель пішла, не перемкнулось."
    return f"{pin} {'увімкнено' if on else 'вимкнено'}."


def _read(entry: dict, arguments: dict) -> str:
    pin = arguments.get("pin")
    if pin not in _named(entry, "switch"):
        return "Такого піна немає."
    level = entry["values"].get(pin)
    if level not in (0, 1):
        return f"{pin}: панель ще не сказала."
    return f"{pin}: {level}"


def _turns(text: str, results: list[tuple[dict, str]]) -> list[dict]:
    """The calls and what they came to, in the protocol's own turns."""
    ids = [call.get("id") or f"call_{index}" for index, (call, _) in enumerate(results, 1)]
    calls = []
    for call_id, (call, _) in zip(ids, results):
        arguments = call.get("arguments") or {}
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        calls.append({"id": call_id, "type": "function",
                      "function": {"name": call.get("name", ""), "arguments": arguments}})
    turns: list[dict] = [{"role": "assistant", "content": text, "tool_calls": calls}]
    for call_id, (_, said) in zip(ids, results):
        turns.append({"role": "tool", "tool_call_id": call_id, "content": said})
    return turns


def _tools(entry: dict) -> list[dict]:
    """What the part offers, as tools the model can call."""
    tools: list[dict] = []
    if _named(entry, "show"):
        tools.append(_tool("show", "Show a few words on the panel's screen for a while.", {
            "text": {"type": "string",
                     "description": "Up to 4 short lines of 9 Latin letters, split by \\n."},
            "seconds": {"type": "integer", "description": "1..60, 10 if unsure."},
        }, ["text"]))
    if _named(entry, "light"):
        tools.append(_tool("light", "Turn the panel's light on, off or blinking.", {
            "mode": {"type": "string", "enum": ["on", "off", "blink"]},
        }, ["mode"]))
    pins = _named(entry, "switch")
    if pins:
        tools.append(_tool("switch", "Turn one of the panel's pins on or off — "
                           "whatever is wired to it.", {
            "pin": {"type": "string", "enum": pins},
            "on": {"type": "boolean"},
        }, ["pin", "on"]))
        tools.append(_tool("read_pin", "Read one of the panel's pins: 1 high, 0 low.", {
            "pin": {"type": "string", "enum": pins},
        }, ["pin"]))
    return tools


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }}
