"""Write the screens the panel draws today, as the canvas calls that draw them.

    uv run python tests/firmware/screens/capture.py

The golden file this writes is what the screen engine must still draw: run
it once before a change to how the screen is drawn, never to make a failing
test pass.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "firmware" / "esp32-c3"), str(HERE.parents[2])]

import main  # noqa: E402
from tests.firmware.fakes import FakeCanvas  # noqa: E402

WORDS = ("BOOT", "WIFI", "PAIR", "BRAIN", "IDLE", "HEAR", "THINK", "SPEAK",
         "ALLOW", "ERROR", "OFFLINE")
TIMES = (0, 130, 600, 2600, 5200, 5330)


class Window:
    available, width, height, view_width, view_height = True, 128, 64, 72, 40

    def __init__(self):
        self.canvas = FakeCanvas(72, 40)

    def viewport(self, buffer):
        return self.canvas

    def flush(self, data):
        return True


class Framebuf:
    MONO_VLSB = 0

    @staticmethod
    def FrameBuffer(data, width, height, form):  # noqa: N802 - framebuf's own name
        return object()


def ops(draw) -> list:
    window = Window()
    draw(window)
    return [list(op) for op in window.canvas.ops]


def screens() -> dict[str, list]:
    found: dict[str, list] = {}
    for word in WORDS:
        for t in TIMES:
            found[f"state {word} {t}"] = ops(lambda w: main.show_state(w, word, t, Framebuf))
    found["text hello"] = ops(lambda w: main.show_text(w, ["hello"], Framebuf))
    found["text four"] = ops(lambda w: main.show_text(w, ["pryvit", "nazare", "a b", "z"], Framebuf))
    bits = bytes([0x80] + [0] * 8 + [0x01] * 9 + [0] * 342)
    found["picture"] = ops(lambda w: main.show_picture(w, bits, Framebuf))
    return found


if __name__ == "__main__":
    (HERE / "golden.json").write_text(json.dumps(screens(), ensure_ascii=False, indent=0),
                                      encoding="utf-8")
    print("written", len(screens()))
