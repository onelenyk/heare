"""The one word and the one icon the panel shows.

The display is 1-bit and tiny: 72x40 holds 9x5 characters in MicroPython's
built-in 8x8 font, 128x64 holds 16x8. There is no room for a sentence and no
reason to send one - the device is only ever told what heare is doing, never
what was said. This module therefore knows eleven words, five 8x8 icons and
two layouts, and nothing else.

Drawing goes through a tiny canvas rather than ``framebuf`` directly: the
board supplies a ``FrameBuffer`` wrapper, whose built-in 8x8 font draws the
word, and host tests supply a recorder. The same code runs on both. The
recorder is also how ``frame_ops`` gives tests an exact, diffable trace.

There is no font table and no rasteriser here: the board's font is the one
the display already has, and turning the trace into bytes is a host-only
helper under ``tests/firmware``.
"""

BOOT = "BOOT"
WIFI = "WIFI"
PAIR = "PAIR"
BRAIN = "BRAIN"
IDLE = "IDLE"
HEAR = "HEAR"
THINK = "THINK"
SPEAK = "SPEAK"
ALLOW = "ALLOW"
ERROR = "ERROR"
OFFLINE = "OFFLINE"

# Same order as ``heare_panel.state.STATES``; a host test compares them so
# the renderer cannot drift from the reducer. Kept local so this module
# imports nothing, not even the reducer's dataclasses.
STATES = (
    BOOT,
    WIFI,
    PAIR,
    BRAIN,
    IDLE,
    HEAR,
    THINK,
    SPEAK,
    ALLOW,
    ERROR,
    OFFLINE,
)

WORDS = {
    BOOT: "BOOT",
    WIFI: "WIFI",
    PAIR: "PAIR",
    BRAIN: "BRAIN",
    IDLE: "IDLE",
    HEAR: "HEAR",
    THINK: "THINK",
    SPEAK: "SPEAK",
    ALLOW: "ALLOW",
    ERROR: "ERROR",
    OFFLINE: "OFFLINE",
}

# The large panel has a fifth line to say what the link is doing. The small
# one has no room for it.
DETAILS = {
    BOOT: "WIFI",
    WIFI: "WIFI",
    PAIR: "LINK",
    BRAIN: "LINK",
    IDLE: "LINK",
    HEAR: "LINK",
    THINK: "LINK",
    SPEAK: "LINK",
    ALLOW: "APPROVE",
    ERROR: "FAULT",
    OFFLINE: "NO LINK",
}

# One icon per state. Wi-Fi is the radio coming up; the chain is the live
# link to the brain; the check asks for a hand; the cross and the struck-out
# Wi-Fi are the two ways it can go wrong.
ICON_WIFI = "wifi"
ICON_LINK = "link"
ICON_ALLOW = "allow"
ICON_ERROR = "error"
ICON_OFFLINE = "offline"

_ICONS = {
    BOOT: ICON_WIFI,
    WIFI: ICON_WIFI,
    PAIR: ICON_ALLOW,
    BRAIN: ICON_LINK,
    IDLE: ICON_LINK,
    HEAR: ICON_LINK,
    THINK: ICON_LINK,
    SPEAK: ICON_LINK,
    ALLOW: ICON_ALLOW,
    ERROR: ICON_ERROR,
    OFFLINE: ICON_OFFLINE,
}

# 8 rows of 8 bits; bit 7 is the leftmost pixel.
_ICON_BITS = {
    ICON_WIFI: (
        0x00,
        0x3C,
        0x42,
        0x99,
        0x24,
        0x18,
        0x18,
        0x00,
    ),
    ICON_LINK: (
        0x00,
        0x00,
        0x66,
        0x99,
        0x99,
        0x66,
        0x00,
        0x00,
    ),
    ICON_ALLOW: (
        0x00,
        0x01,
        0x02,
        0x04,
        0x88,
        0x50,
        0x20,
        0x00,
    ),
    ICON_ERROR: (
        0x00,
        0x42,
        0x24,
        0x18,
        0x18,
        0x24,
        0x42,
        0x00,
    ),
    ICON_OFFLINE: (
        0x80,
        0x7C,
        0x62,
        0x99,
        0x2C,
        0x1C,
        0x1A,
        0x01,
    ),
}

CELL = 8
BLINK_MS = 500

# (word row, icon row, detail row or None)
_LAYOUTS = {
    (72, 40): (0, 3, None),
    (128, 64): (0, 2, 5),
}


class _Layout:
    __slots__ = ("detail_row", "icon_row", "word_row")

    def __init__(self, word_row, icon_row, detail_row):
        self.word_row = word_row
        self.icon_row = icon_row
        self.detail_row = detail_row


def _layout(width, height):
    rows = _LAYOUTS.get((width, height))
    if rows is None:
        raise ValueError(
            f"no layout for {width}x{height}; expected 72x40 or 128x64"
        )
    return _Layout(rows[0], rows[1], rows[2])


def render(state, canvas, width, height, now_ms=0):
    """Draw one frame; ALLOW blinks off for the second half of each second.

    ``now_ms`` is only consulted for ALLOW, so every other state renders the
    same bytes at any time and a test can pass zero. The canvas is cleared
    first, including on the dark half of a blink, so the previous frame
    cannot show through.
    """
    if state not in WORDS:
        raise ValueError(f"unknown panel state: {state!r}")
    layout = _layout(width, height)

    canvas.fill(0)
    if state == ALLOW and (now_ms // BLINK_MS) % 2:
        return

    word = WORDS[state]
    canvas.text(
        word,
        max((width - CELL * len(word)) // 2, 0),
        layout.word_row * CELL,
        1,
    )

    icon = _ICON_BITS[_ICONS[state]]
    icon_x = (width - CELL) // 2
    icon_y = layout.icon_row * CELL
    for row in range(CELL):
        bits = icon[row]
        for col in range(CELL):
            if bits & (1 << (7 - col)):
                canvas.pixel(icon_x + col, icon_y + row, 1)

    if layout.detail_row is not None:
        detail = DETAILS[state]
        canvas.text(
            detail,
            max((width - CELL * len(detail)) // 2, 0),
            layout.detail_row * CELL,
            1,
        )


def frame_ops(state, width, height, now_ms=0):
    """The frame as a list of canvas calls, for golden tests."""
    recorder = _Recorder()
    render(state, recorder, width, height, now_ms)
    return recorder.ops


class _Recorder:
    """A canvas that records ops exactly like ``tests.firmware.fakes``."""

    def __init__(self):
        self.ops = []

    def fill(self, color):
        self.ops.append(("fill", color))

    def fill_rect(self, x, y, w, h, color):
        self.ops.append(("fill_rect", x, y, w, h, color))

    def rect(self, x, y, w, h, color):
        self.ops.append(("rect", x, y, w, h, color))

    def text(self, string, x, y, color=1):
        self.ops.append(("text", string, x, y, color))

    def pixel(self, x, y, color=1):
        self.ops.append(("pixel", x, y, color))

    def hline(self, x, y, w, color=1):
        self.ops.append(("hline", x, y, w, color))

    def vline(self, x, y, h, color=1):
        self.ops.append(("vline", x, y, h, color))
