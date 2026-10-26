"""The native hands: quick, simple acts on the bodies, with no agent and no session.

    uv run python -m heare.native_hands

The opencode hand takes seconds to start and a long prompt to read; turning on
a light needs neither. This hand is a part like the others: it knocks as an
executor, claims work, and does it with the voice's model and the acts the
other parts named (``acts`` in their knock) as its tools, in a few calls at
most. The dispatcher chooses between the hands by their ``about``; whatever
needs a plan, files, code or questions belongs to the opencode hand.

Showing and lighting change nothing that matters and are done at once. A pin
may be wired to anything, so it goes through the desk's ``judge``: what the
person plainly asked for is done, anything else is asked.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import socket
import sys
from typing import AsyncIterator, Protocol

import httpx

from heare import hand as hand_life
from heare import layout, logs, part
from heare.config import Settings
from heare.core.forms import Desk, Model

logger = logging.getLogger("heare.native_hands")

CHANNEL = "native-hands"
ABOUT = (
    "плата heare на столі з маленьким OLED-екраном: вивести на OLED кілька "
    "слів, світло на платі, увімкнути чи вимкнути пін, прочитати пін. Плата "
    "вже під'єднана, шукати її не треба. Одна-дві дії, без плану, без файлів, "
    "коду, шелу й інтернету."
)
STEPS = 4
SHOW_S = 10
SHOW_MAX_S = 60
SHOW_CHARS = 40
MODEL_TIMEOUT_S = 20.0
FAILED = "Не вийшло: модель не відповіла."
# The OLED's font has Latin letters only; Ukrainian is written out in them
# rather than drawn as noise.
_LATIN = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e",
    "є": "ie", "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "i", "й": "i",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "shch", "ь": "", "ю": "iu", "я": "ia", "ы": "y", "э": "e",
    "ё": "io", "ъ": "", "’": "", "'": "",
})

_SYSTEM = (
    "Ти — швидкі руки heare. Зроби прохання інструментами й відповідай одним "
    "коротким реченням українською: що зроблено. Не вигадуй того, чого "
    "інструменти не сказали. Якщо прохання не про ці інструменти — скажи "
    "одним реченням, що це не твоє. На екран пиши латиницею: кирилиці він "
    "не має."
)


class Devices(Protocol):
    """The other parts' acts, as the brain tells them."""

    async def acting(self) -> dict[str, dict]:
        """By pid: the part's channel, the acts it offers, its last values."""
        ...

    async def act(self, pid: str, name: str, value: object) -> bool: ...


class NativeHand:
    about = ABOUT

    def __init__(self, model: Model, devices: Devices) -> None:
        self._model = model
        self._devices = devices

    async def run(self, task: str, desk: Desk) -> str:
        here = await _found(self._devices)
        if here is None:
            return "Жодної частини з діями зараз немає."
        tools = _tools(here[1])
        messages: list[dict] = [{"role": "system", "content": _SYSTEM},
                                {"role": "user", "content": task}]
        for _ in range(STEPS):
            desk.step("думаю")
            text, calls = await self._turn(messages, tools)
            if getattr(desk, "stopped", False):
                raise asyncio.CancelledError
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
        here = await _found(self._devices)
        if here is None:
            return "Частина пішла."
        pid, entry = here
        name = call.get("name")
        desk.step(str(name))
        if name == "show":
            return await _show(self._devices, pid, entry, arguments)
        if name == "light":
            return await _light(self._devices, pid, entry, arguments)
        if name == "switch":
            return await _switch(self._devices, pid, entry, arguments, desk)
        if name == "read_pin":
            return _read(entry, arguments)
        return "Такого інструмента немає."


async def _found(devices: Devices) -> tuple[str, dict] | None:
    """The part to act through: the first, while there is one panel."""
    return next(iter((await devices.acting()).items()), None)


def _named(entry: dict, kind: str) -> list[str]:
    return [a["name"] for a in entry["acts"] if a["kind"] == kind]


async def _show(devices: Devices, pid: str, entry: dict, arguments: dict) -> str:
    text = latin(str(arguments.get("text") or "")).strip()[:SHOW_CHARS]
    if not _named(entry, "show") or not text:
        return "Нема що показати."
    try:
        seconds = int(arguments.get("seconds") or SHOW_S)
    except (TypeError, ValueError):
        seconds = SHOW_S
    seconds = max(1, min(seconds, SHOW_MAX_S))
    await devices.act(pid, _named(entry, "show")[0], {"text": text, "seconds": seconds})
    return f"Показано на {seconds} с."


async def _light(devices: Devices, pid: str, entry: dict, arguments: dict) -> str:
    mode = arguments.get("mode")
    if not _named(entry, "light") or mode not in ("on", "off", "blink"):
        return "Світло не зрозуміло, що робити."
    await devices.act(pid, _named(entry, "light")[0], mode)
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
    if not await devices.act(pid, pin, on):
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


# -- the part: the brain's door, the model, the desk -------------------------


class Parts:
    """The other parts' acts, over the door."""

    def __init__(self, wire: part.Wire) -> None:
        self._wire = wire

    async def acting(self) -> dict[str, dict]:
        try:
            answer = await self._wire.client.get(f"{self._wire.base}/api/acts")
        except httpx.HTTPError:
            return {}
        return answer.json() if answer.status_code == 200 else {}

    async def act(self, pid: str, name: str, value: object) -> bool:
        answer = await self._wire.post("/api/acts", {"part": pid, "name": name, "value": value})
        return answer.status_code == 200


class Chat:
    """The voice's model, asked once per turn, with tools.

    Its own few lines rather than the brain's client: a part never imports
    another part. Only providers that speak the OpenAI wire are used; the
    rest of the choice — which provider, which model, which key — is the
    same settings the voice reads.
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client

    async def reply(self, messages: list[dict], verbs: list[dict]) -> AsyncIterator[dict]:
        url, key, model = self._chosen()
        body: dict = {"model": model, "messages": messages, "temperature": 0.2}
        if verbs:
            body["tools"] = verbs
        answer = await self._client.post(
            f"{url}/chat/completions", json=body, timeout=MODEL_TIMEOUT_S,
            headers={"Authorization": f"Bearer {key}"})
        answer.raise_for_status()
        said = (answer.json().get("choices") or [{}])[0].get("message") or {}
        if said.get("content"):
            yield {"type": "delta", "text": said["content"]}
        for call in said.get("tool_calls") or []:
            yield {"type": "tool_call", "id": call.get("id"),
                   "name": call["function"]["name"],
                   "arguments": call["function"].get("arguments") or "{}"}

    def _chosen(self) -> tuple[str, str, str]:
        from heare.model.providers import PROVIDERS

        wanted = str(self._settings.llm_provider or "deepseek").lower()
        for name in [wanted] + [k for k in PROVIDERS if k != wanted]:
            cfg = PROVIDERS.get(name)
            key = getattr(self._settings, cfg.api_key_attr, None) if cfg else None
            if cfg is None or not key or cfg.api_style != "openai":
                continue
            url = getattr(self._settings, f"{name}_base_url", "") or cfg.base_url
            model = getattr(self._settings, f"{name}_model", "") or cfg.default_model
            return url.rstrip("/"), key, model
        raise RuntimeError("немає ключа до моделі з OpenAI-протоколом")


def latin(text: str) -> str:
    """Ukrainian written in Latin letters, case kept, for the OLED's font."""
    out = []
    for char in text:
        low = char.lower()
        spelled = low.translate(_LATIN)
        out.append(spelled.capitalize() if char != low and spelled else spelled)
    return "".join(out)


async def run(settings: Settings | None = None, model: Model | None = None,
              host: str | None = None) -> int:
    """The part's life; a bench hands in its own brain, model and host."""
    settings = settings or Settings()
    host = host or socket.gethostname()
    async with httpx.AsyncClient(timeout=None) as client:

        async def work(wire: part.Wire) -> None:
            hand = NativeHand(model or Chat(settings, client), Parts(wire))
            wire.report({"work": "чекає"})

            async def do(job: hand_life.Job, desk: hand_life.Desk) -> str:
                return await hand.run(job.request, desk)

            await hand_life.serve(
                wire, CHANNEL, do,
                on_begin=lambda job, desk: wire.report({"work": f"роблю: {job.request[:50]}"}),
                on_end=lambda job, status, result: wire.report({"work": "чекає"}),
                # The person hears a sentence; the reason goes to the log.
                failed=lambda exc: FAILED)

        return await part.live(
            {"channel": CHANNEL, "forms": ["executor"], "about": ABOUT,
             "key": f"{CHANNEL}@{host}", "host": host,
             "ui": [{"name": "work", "kind": "label", "label": "Справа",
                     "value": "чекає", "head": True}]},
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
