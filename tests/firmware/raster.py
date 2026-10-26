"""A canvas that sets pixels, for tests and previews that look at the picture.

The board's Latin letters are not known on a laptop: ``letters`` stands in
for them (by default a filled 6×7 box per letter, which shows where text
sits); the Ukrainian ones and everything else are the firmware's own.
"""

from __future__ import annotations


class Raster:
    def __init__(self, width: int = 72, height: int = 40, letters=None) -> None:
        self.width, self.height = width, height
        self.lit = [[False] * width for _ in range(height)]
        self.letters = letters

    def pixel(self, x, y, color=1):
        if 0 <= x < self.width and 0 <= y < self.height:
            self.lit[y][x] = bool(color)

    def fill(self, color):
        self.fill_rect(0, 0, self.width, self.height, color)

    def fill_rect(self, x, y, w, h, color):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.pixel(xx, yy, color)

    def hline(self, x, y, w, color=1):
        self.fill_rect(x, y, w, 1, color)

    def vline(self, x, y, h, color=1):
        self.fill_rect(x, y, 1, h, color)

    def rect(self, x, y, w, h, color=1):
        self.hline(x, y, w, color)
        self.hline(x, y + h - 1, w, color)
        self.vline(x, y, h, color)
        self.vline(x + w - 1, y, h, color)

    def text(self, string, x, y, color=1):
        for index, char in enumerate(string):
            left = x + index * 8
            rows = self.letters(char) if self.letters else None
            if rows is None:
                if char != " ":
                    self.fill_rect(left, y, 6, 7, color)
                continue
            for row in range(8):
                for column in range(8):
                    if rows[row] & (0x80 >> column):
                        self.pixel(left + column, y + row, color)

    def ascii(self) -> str:
        return "\n".join("".join("#" if on else "." for on in row) for row in self.lit)

    def count(self) -> int:
        return sum(sum(row) for row in self.lit)
