"""A clipped window onto a larger 1-bit canvas.

The board has a 128x64 controller but only a 72x40 window of it is visible,
at (28,24). ``render`` knows the 72x40 layout, not where that window sits in
the controller, so this wrapper adds the offset and refuses to draw outside
the window. The same renderer therefore works on a laptop and on the board.

``CELL`` is the width of one character in the display's built-in 8x8 font,
not a font table: it is only used to decide which whole characters still
fit inside the window.
"""

CELL = 8


class Viewport:
    """A canvas that offsets and clips every primitive to one rectangle."""

    __slots__ = ("_canvas", "height", "width", "xoff", "yoff")

    def __init__(self, canvas, xoff, yoff, width, height):
        self._canvas = canvas
        self.xoff = xoff
        self.yoff = yoff
        self.width = width
        self.height = height

    def fill(self, color):
        if self.width <= 0 or self.height <= 0:
            return
        self._canvas.fill_rect(
            self.xoff, self.yoff, self.width, self.height, color
        )

    def fill_rect(self, x, y, w, h, color):
        x0 = max(x, 0)
        y0 = max(y, 0)
        x1 = min(x + w, self.width)
        y1 = min(y + h, self.height)
        if x1 <= x0 or y1 <= y0:
            return
        self._canvas.fill_rect(
            self.xoff + x0, self.yoff + y0, x1 - x0, y1 - y0, color
        )

    def rect(self, x, y, w, h, color):
        if w <= 0 or h <= 0:
            return
        self.hline(x, y, w, color)
        self.hline(x, y + h - 1, w, color)
        self.vline(x, y, h, color)
        self.vline(x + w - 1, y, h, color)

    def text(self, string, x, y, color=1):
        if not string:
            return
        if y < 0 or y + CELL > self.height:
            return
        start = 0
        if x < 0:
            start = (-x + CELL - 1) // CELL
        end = len(string)
        if x + end * CELL > self.width:
            end = min((self.width - x) // CELL, len(string))
        if start >= end:
            return
        self._canvas.text(
            string[start:end],
            self.xoff + x + start * CELL,
            self.yoff + y,
            color,
        )

    def pixel(self, x, y, color=1):
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            return
        self._canvas.pixel(self.xoff + x, self.yoff + y, color)

    def hline(self, x, y, w, color=1):
        if y < 0 or y >= self.height:
            return
        x0 = max(x, 0)
        x1 = min(x + w, self.width)
        if x1 <= x0:
            return
        self._canvas.hline(self.xoff + x0, self.yoff + y, x1 - x0, color)

    def vline(self, x, y, h, color=1):
        if x < 0 or x >= self.width:
            return
        y0 = max(y, 0)
        y1 = min(y + h, self.height)
        if y1 <= y0:
            return
        self._canvas.vline(self.xoff + x, self.yoff + y0, y1 - y0, color)
