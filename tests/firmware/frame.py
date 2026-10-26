"""Host-only rasteriser for the panel's 1-bit frame.

The firmware never carries a font: on the board ``framebuf.FrameBuffer``
draws text with MicroPython's built-in 8x8 font. A laptop has no framebuf,
so this helper supplies a small font and the same page-packed byte layout
for tests that need to look at actual pixels. It imports the renderer, never
the other way round.
"""

from heare_panel import render

CELL = render.CELL

_FONT = {
    " ": (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00),
    "A": (0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11),
    "B": (0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E),
    "D": (0x1E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1E),
    "E": (0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F),
    "F": (0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10),
    "H": (0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11),
    "I": (0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x1F),
    "K": (0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11),
    "L": (0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F),
    "N": (0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11),
    "O": (0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E),
    "P": (0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10),
    "R": (0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11),
    "S": (0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E),
    "T": (0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04),
    "U": (0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E),
    "V": (0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04),
    "W": (0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11),
}


def frame(state: str, width: int, height: int, now_ms: int = 0) -> bytearray:
    """Rasterise one frame into page-packed bytes.

    Bit 0 of each byte is the leftmost pixel of its group of eight, rows run
    top to bottom, and each row starts at ``y * width // 8``. ``width`` must
    be a multiple of eight.
    """
    if width % 8:
        raise ValueError(f"frame width must be a multiple of 8, not {width}")
    buffer = _FrameBuffer(width, height)
    render.render(state, buffer, width, height, now_ms)
    return buffer.data


class _FrameBuffer:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.data = bytearray((width * height) // 8)

    def _set(self, x: int, y: int, color: int) -> None:
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            return
        index = y * (self.width // 8) + (x >> 3)
        bit = 1 << (x & 7)
        if color:
            self.data[index] |= bit
        else:
            self.data[index] &= ~bit & 0xFF

    def fill(self, color: int) -> None:
        value = 0xFF if color else 0x00
        for index in range(len(self.data)):
            self.data[index] = value

    def fill_rect(self, x: int, y: int, w: int, h: int, color: int) -> None:
        for row in range(y, y + h):
            for col in range(x, x + w):
                self._set(col, row, color)

    def rect(self, x: int, y: int, w: int, h: int, color: int) -> None:
        self.hline(x, y, w, color)
        self.hline(x, y + h - 1, w, color)
        self.vline(x, y, h, color)
        self.vline(x + w - 1, y, h, color)

    def text(self, string: str, x: int, y: int, color: int = 1) -> None:
        for offset, char in enumerate(string):
            glyph = _FONT.get(char)
            if glyph is None:
                continue
            left = x + offset * CELL
            for row in range(7):
                bits = glyph[row]
                for col in range(5):
                    if bits & (1 << (4 - col)):
                        self._set(left + col, y + row, color)

    def pixel(self, x: int, y: int, color: int = 1) -> None:
        self._set(x, y, color)

    def hline(self, x: int, y: int, w: int, color: int = 1) -> None:
        for col in range(x, x + w):
            self._set(col, y, color)

    def vline(self, x: int, y: int, h: int, color: int = 1) -> None:
        for row in range(y, y + h):
            self._set(x, row, color)
