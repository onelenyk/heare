"""Virtual boards: parts that knock and act like the ESP32 panel, in Python.

    uv run heare-sim --board desk --board kitchen

A bench for the hands and the bodies without a board on the desk: each
virtual board knocks with the same ``acts`` the panel's firmware names, does
what the brain tells it, remembers it (``Board.done``, ``Board.shown``,
``Board.pins``) and reports its pins as the panel does. Any number of them
run in one process, each a part of its own. A board can be told to be slow
or to drop what it is told, so the bench can see what the hands do then.

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
# The panel's own: the screen, the light, and the pins nothing on it uses.
FREE_PINS = (0, 1, 2, 3, 4, 7, 10, 20, 21)
PANEL_ACTS = (
    [{"name": "show", "kind": "show"}, {"name": "led", "kind": "light"}]
    + [{"name": f"pin{n}", "kind": "switch"} for n in FREE_PINS]
)


@dataclass
class Board:
    """One virtual board: what it offers and what it was told."""

    name: str
    about: str = "віртуальна плата з OLED-екраном"
    acts: list[dict] = field(default_factory=lambda: [dict(a) for a in PANEL_ACTS])
    # Seconds before an act is done; and whether acts are dropped unheard.
    slow: float = 0.0
    deaf: bool = False
    done: list[tuple[str, object]] = field(default_factory=list)
    shown: str = ""
    led: str = "off"
    pins: dict[str, int] = field(default_factory=dict)
    let_in: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def channel(self) -> str:
        return f"oled-{self.name}"

    @property
    def pid(self) -> str:
        return f"{self.channel}@{HOST}"

    def knock(self) -> dict:
        return {"channel": self.channel, "forms": ["output"], "about": self.about,
                "key": self.pid, "host": HOST, "acts": self.acts,
                "ui": [{"name": "now", "kind": "label", "label": "Екран",
                        "value": "", "head": True}]}

    def levels(self) -> dict[str, int]:
        return {a["name"]: self.pins.get(a["name"], 0)
                for a in self.acts if a["kind"] == "switch"}

    def apply(self, name: str, value: object) -> bool:
        """Do one act the way the panel does; False for what it would drop."""
        named = {a["name"]: a["kind"] for a in self.acts}
        kind = named.get(name)
        if kind == "show" and isinstance(value, dict) and value.get("text"):
            self.shown = str(value["text"])
        elif kind == "light" and value in ("on", "off", "blink"):
            self.led = str(value)
        elif kind == "switch" and isinstance(value, bool):
            self.pins[name] = 1 if value else 0
        else:
            return False
        self.done.append((name, value))
        return True


async def live(board: Board, settings: Settings) -> int:
    """Keep one virtual board in the brain until cancelled."""
    wire_of: dict[str, part.Wire] = {}

    def on_event(event: dict) -> None:
        if event.get("kind") != "control" or event.get("part") != board.pid or board.deaf:
            return
        asyncio.get_running_loop().create_task(act(event))

    async def act(event: dict) -> None:
        if board.slow:
            await asyncio.sleep(board.slow)
        if board.apply(str(event.get("name", "")), event.get("value")):
            wire = wire_of.get("wire")
            if wire is not None:
                wire.report({"now": board.shown or "IDLE", **board.levels()})

    async def work(wire: part.Wire) -> None:
        wire_of["wire"] = wire
        wire.report({"now": "IDLE", **board.levels()})
        board.let_in.set()
        await asyncio.Event().wait()

    return await part.live(board.knock(), work, on_event=on_event,
                           hello=f"віртуальна плата {board.name}", settings=settings)


async def run(names: list[str], settings: Settings | None = None) -> int:
    settings = settings or Settings()
    boards = [Board(name) for name in names]
    codes = await asyncio.gather(*(live(b, settings) for b in boards))
    return max(codes, default=0)


def main() -> int:
    parser = argparse.ArgumentParser(prog="heare.sim")
    parser.add_argument("--board", action="append", default=[],
                        help="ім'я віртуальної плати; можна кілька")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logs.setup(args.verbose)
    layout.settle()
    try:
        return asyncio.run(run(args.board or ["sim"]))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
