"""Virtual boards: parts that knock and act like the ESP32 panel, in Python.

    uv run heare-sim --board desk --board kitchen --sensor kitchen

A bench for the hands and the bodies without a board on the desk. Each
virtual board knocks with its skills (``heare.api.skills``), answers every
call it gets, remembers what it was told (``done``, ``shown``, ``led``,
``pins``) and reports as the panel does. Any number run in one process, each
a part of its own. A board can be slow, deaf (drops calls unanswered),
or carry a sensor, so the bench sees what the hands do then.

Not a part of the app: nothing installs it, the keeper never starts it.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field

from heare import layout, logs, part
from heare.config import Settings

HOST = "sim"
# The panel's own: the pins nothing on it uses.
FREE_PINS = (0, 1, 2, 3, 4, 7, 10, 20, 21)


def panel_skills() -> list[dict]:
    """What the panel offers, in the shape its firmware will knock with."""
    found = [
        {"name": "show", "about": "Показати кілька слів на OLED-екрані на кілька секунд",
         "effect": "harmless", "params": {"type": "object", "required": ["text"], "properties": {
             "text": {"type": "string", "description": "до 4 рядків по 9 літер"},
             "seconds": {"type": "integer", "description": "1..60"}}}},
        {"name": "light", "about": "Світлодіод: on, off або blink (блимає сам)",
         "effect": "harmless", "params": {"type": "object", "required": ["mode"], "properties": {
             "mode": {"type": "string", "enum": ["on", "off", "blink"]}}}},
        {"name": "image", "about": "Намалювати картинку на OLED 72×40",
         "effect": "harmless", "params": {"type": "object", "required": ["svg"], "properties": {
             "svg": {"type": "string"}, "seconds": {"type": "integer"}}},
         "render": {"svg": {"to": "mono", "width": 72, "height": 40, "as": "bitmap"}}},
        {"name": "screen", "about": "Показати сцену: розкладка з віджетів і поля",
         "effect": "harmless", "params": {"type": "object", "required": ["scene"], "properties": {
             "scene": {"type": "object"}, "layer": {"type": "string"},
             "seconds": {"type": "integer"}}},
         "render": {"scene": [
             {"deep": True, "key": "svg", "where": {"widget": "icon"},
              "to": "mono", "width": 16, "height": 16, "as": "bits"},
             {"deep": True, "key": "svg", "where": {"widget": "picture"},
              "to": "mono", "width": 72, "height": 40, "as": "bits"}]}},
        {"name": "update", "about": "Змінити поля сцени", "effect": "harmless",
         "params": {"type": "object", "required": ["id", "fields"], "properties": {
             "id": {"type": "string"}, "fields": {"type": "object"}}}},
        {"name": "read_pins", "about": "Рівні вільних пінів: 1 високий, 0 низький",
         "effect": "read", "params": {"type": "object", "properties": {}}},
    ]
    for n in FREE_PINS:
        found.append({"name": f"pin{n}", "about": f"Пін {n}: увімкнути чи вимкнути те, що під'єднано",
                      "effect": "change", "params": {"type": "object", "required": ["on"],
                                                     "properties": {"on": {"type": "boolean"}}}})
    return found


SENSOR = {"name": "temperature", "about": "Температура в кімнаті, °C",
          "effect": "read", "params": {"type": "object", "properties": {}}}


@dataclass
class Board:
    """One virtual board: what it offers and what it was told."""

    name: str
    about: str = "віртуальна плата з OLED-екраном"
    host: str = HOST
    skills: list[dict] = field(default_factory=panel_skills)
    # Seconds before a call is done; and calls dropped unanswered.
    slow: float = 0.0
    deaf: bool = False
    temperature: float = 21.5
    done: list[tuple[str, object]] = field(default_factory=list)
    shown: str = ""
    picture: bytes = b""
    scenes: dict[str, dict] = field(default_factory=dict)
    led: str = "off"
    pins: dict[str, int] = field(default_factory=dict)
    let_in: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def channel(self) -> str:
        return f"oled-{self.name}"

    @property
    def pid(self) -> str:
        return f"{self.channel}@{self.host}"

    def knock(self) -> dict:
        return {"channel": self.channel, "name": self.name, "forms": ["output"],
                "about": self.about, "key": self.pid, "host": self.host,
                "skills": self.skills,
                "ui": [{"name": "now", "kind": "label", "label": "Екран",
                        "value": "", "head": True}]}

    def levels(self) -> dict[str, int]:
        return {f"pin{n}": self.pins.get(f"pin{n}", 0) for n in FREE_PINS}

    def call(self, name: str, args: dict) -> tuple[bool, object]:
        """One skill, done the way the panel does it: (ok, result)."""
        if name not in {s["name"] for s in self.skills}:
            return False, "такого скіла немає"
        if name == "show" and args.get("text"):
            self.shown = str(args["text"])
            result: object = f"показано: {self.shown}"
        elif name == "light" and args.get("mode") in ("on", "off", "blink"):
            self.led = str(args["mode"])
            result = f"світло: {self.led}"
        elif name == "image" and args.get("bitmap"):
            import base64

            self.picture = base64.b64decode(str(args["bitmap"]))
            if len(self.picture) != 360:
                return False, "картинка має бути 72x40"
            result = "картинку показано"
        elif name == "screen" and isinstance(args.get("scene"), dict):
            scene = args["scene"]
            self.scenes[str(scene.get("id") or "scene")] = scene
            result = f"сцену {scene.get('id')} показано"
        elif name == "update" and str(args.get("id")) in self.scenes:
            self.scenes[str(args["id"])].setdefault("fields", {}).update(args.get("fields") or {})
            result = f"оновлено: {args['id']}"
        elif name == "read_pins":
            return True, self.levels()
        elif name == "temperature":
            return True, self.temperature
        elif name.startswith("pin") and isinstance(args.get("on"), bool):
            self.pins[name] = 1 if args["on"] else 0
            result = f"{name}: {self.pins[name]}"
        else:
            return False, "не той аргумент"
        self.done.append((name, args))
        return True, result


async def live(board: Board, settings: Settings) -> int:
    """Keep one virtual board in the brain until cancelled."""
    held: dict[str, part.Wire] = {}

    def on_event(event: dict) -> None:
        if event.get("part") != board.pid or board.deaf:
            return
        if event.get("kind") == "call":
            asyncio.get_running_loop().create_task(answer(event))

    async def answer(event: dict) -> None:
        if board.slow:
            await asyncio.sleep(board.slow)
        wire = held["wire"]
        args = event.get("args") if isinstance(event.get("args"), dict) else {}
        ok, result = board.call(str(event.get("name", "")), args)
        await wire.post(f"/api/skills/{event['id']}", {"ok": ok, "result": result})
        wire.report({"now": board.shown or "IDLE", **board.levels()})

    async def work(wire: part.Wire) -> None:
        held["wire"] = wire
        wire.report({"now": "IDLE", **board.levels()})
        board.let_in.set()
        await asyncio.Event().wait()

    return await part.live(board.knock(), work, on_event=on_event,
                           hello=f"віртуальна плата {board.name}", settings=settings)


async def run(names: list[str], sensors: list[str], settings: Settings | None = None) -> int:
    settings = settings or Settings()
    boards = [Board(name) for name in names]
    for board in boards:
        if board.name in sensors:
            board.skills.append(dict(SENSOR))
    codes = await asyncio.gather(*(live(b, settings) for b in boards))
    return max(codes, default=0)


def main() -> int:
    parser = argparse.ArgumentParser(prog="heare.sim")
    parser.add_argument("--board", action="append", default=[],
                        help="ім'я віртуальної плати; можна кілька")
    parser.add_argument("--sensor", action="append", default=[],
                        help="плата, що має ще й датчик температури")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logs.setup(args.verbose)
    layout.settle()
    try:
        return asyncio.run(run(args.board or ["sim"], args.sensor))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
