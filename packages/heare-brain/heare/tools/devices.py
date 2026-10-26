"""The parts the agent may act through: the panel's screen, light and pins.

Showing and lighting change nothing that matters and are done at once. A
switch may be wired to anything, so it waits for the person's yes — and that
wait cannot hold the turn: the yes comes from the person's own next words,
which a held turn would never hear. The switch is asked for in the
background and its outcome said when it comes.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from heare.tools.ports import Devices

SHOW_S = 10
SHOW_MAX_S = 60
SHOW_CHARS = 40

_held: set[asyncio.Task] = set()


def found(devices: Devices | None) -> tuple[str, dict] | None:
    """The one part to act through; the first, while there is one panel."""
    acting = devices.acting() if devices is not None else {}
    for pid, entry in acting.items():
        return pid, entry
    return None


def pins(entry: dict) -> list[str]:
    return [a["name"] for a in entry["acts"] if a["kind"] == "switch"]


def _has(entry: dict, kind: str) -> str | None:
    return next((a["name"] for a in entry["acts"] if a["kind"] == kind), None)


async def run(devices: Devices, ask: Callable[[str, str], Awaitable[bool]],
              say: Callable[[str, str], None], arguments: dict) -> str:
    here = found(devices)
    if here is None:
        return "Панелі зараз немає."
    pid, entry = here
    action = arguments.get("action")
    if action == "show":
        name = _has(entry, "show")
        text = str(arguments.get("text") or "").strip()[:SHOW_CHARS]
        if name is None or not text:
            return "Нема що показати."
        try:
            seconds = int(arguments.get("seconds") or SHOW_S)
        except (TypeError, ValueError):
            seconds = SHOW_S
        seconds = max(1, min(seconds, SHOW_MAX_S))
        devices.act(pid, name, {"text": text, "seconds": seconds})
        return "Показав на панелі."
    if action == "light":
        name = _has(entry, "light")
        mode = arguments.get("mode")
        if name is None or mode not in ("on", "off", "blink"):
            return "Не зрозумів, що зробити зі світлом."
        devices.act(pid, name, mode)
        return "Готово."
    if action == "switch":
        pin = arguments.get("pin")
        if pin not in pins(entry):
            return "Такого піна на панелі немає."
        on = arguments.get("on") is True
        job = asyncio.create_task(_switch(devices, ask, say, pid, entry, pin, on))
        _held.add(job)
        job.add_done_callback(_held.discard)
        return "Питаю дозволу."
    return "Не зрозумів, що зробити з панеллю."


async def _switch(devices: Devices, ask: Callable[[str, str], Awaitable[bool]],
                  say: Callable[[str, str], None], pid: str, entry: dict,
                  pin: str, on: bool) -> None:
    what = f"{'увімкнути' if on else 'вимкнути'} {pin} на панелі"
    channel = entry["channel"]
    if not await ask(channel, what):
        say(channel, f"{pin} не чіпаю: дозволу не було.")
        return
    if not devices.act(pid, pin, on):
        say(channel, "Панель пішла, перемкнути не вийшло.")
        return
    say(channel, f"{pin} {'увімкнено' if on else 'вимкнено'}.")


def read(devices: Devices | None, arguments: dict) -> str:
    here = found(devices)
    if here is None:
        return "Панелі зараз немає."
    _pid, entry = here
    pin = arguments.get("pin")
    if pin not in pins(entry):
        return "Такого піна на панелі немає."
    level = entry["values"].get(pin)
    if level not in (0, 1):
        return f"{pin}: панель ще не сказала."
    return f"{pin}: {level}"
