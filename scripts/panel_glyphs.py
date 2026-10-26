"""Write heare_panel/glyphs.py: the Ukrainian letters and the icons, as bytes.

    uv run --no-project --with resvg-py python scripts/panel_glyphs.py

The board's own font is Latin only. Letters that look like Latin ones are
drawn with it (``LOOKALIKE``); the rest are drawn here, eight rows of eight
pixels, in the board font's shape (six wide, caps seven tall, the eighth row
for descenders). Icons are written as small SVGs and drawn at 16×16 with the
same renderer the hands use. The board carries only the bytes: rows top to
bottom, the leftmost pixel the highest bit.
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "firmware" / "esp32-c3" / "heare_panel" / "glyphs.py"

LOOKALIKE = {
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P",
    "С": "C", "Т": "T", "Х": "X", "І": "I", "а": "a", "е": "e", "о": "o", "р": "p",
    "с": "c", "у": "y", "х": "x", "і": "i", "У": "Y", "’": "'", "ʼ": "'", "«": '"',
    "»": '"', "—": "-", "–": "-", "…": ".",
}

LETTERS = {
    "Б": "######|#.....|#.....|#####.|#....#|#....#|#####.|......",
    "Г": "######|#.....|#.....|#.....|#.....|#.....|#.....|......",
    "Ґ": ".....#|######|#.....|#.....|#.....|#.....|#.....|......",
    "Д": "..###.|.#..#.|.#..#.|.#..#.|.#..#.|######|#....#|......",
    "Є": ".####.|#....#|#.....|####..|#.....|#....#|.####.|......",
    "Ж": "#..#..#|.#.#.#.|..###..|..###..|.#.#.#.|#..#..#|#..#..#|.......",
    "З": ".####.|#....#|.....#|..###.|.....#|#....#|.####.|......",
    "И": "#....#|#...##|#..#.#|#.#..#|##...#|#....#|#....#|......",
    "Й": ".#..#.|..##..|#....#|#...##|#..#.#|#.#..#|##...#|......",
    "Л": "..####|.#...#|.#...#|.#...#|.#...#|.#...#|#....#|......",
    "П": "######|#....#|#....#|#....#|#....#|#....#|#....#|......",
    "Ф": "..#...|.###..|#.#.#.|#.#.#.|.###..|..#...|..#...|......",
    "Ц": "#...#.|#...#.|#...#.|#...#.|#...#.|######|.....#|......",
    "Ч": "#....#|#....#|#....#|.#####|.....#|.....#|.....#|......",
    "Ш": "#.#.#.|#.#.#.|#.#.#.|#.#.#.|#.#.#.|#.#.#.|######|......",
    "Щ": "#.#.#.|#.#.#.|#.#.#.|#.#.#.|#.#.#.|######|.....#|......",
    "Ь": "#.....|#.....|#.....|#####.|#....#|#....#|#####.|......",
    "Ю": "#..##.|#.#..#|#.#..#|###..#|#.#..#|#.#..#|#..##.|......",
    "Я": ".#####|#....#|#....#|.#####|...#.#|..#..#|.#...#|......",
    "Ї": ".#.#..|......|.###..|..#...|..#...|..#...|.###..|......",
    "б": "..###.|.#....|#.....|#####.|#....#|#....#|.####.|......",
    "в": "......|......|#####.|#....#|#####.|#....#|#####.|......",
    "г": "......|......|######|#.....|#.....|#.....|#.....|......",
    "ґ": "......|.....#|######|#.....|#.....|#.....|#.....|......",
    "д": "......|......|..###.|.#..#.|.#..#.|######|#....#|......",
    "є": "......|......|.####.|#.....|####..|#.....|.####.|......",
    "ж": ".......|.......|#..#..#|.#.#.#.|..###..|.#.#.#.|#..#..#|.......",
    "з": "......|......|.####.|.....#|..###.|.....#|.####.|......",
    "и": "......|......|#....#|#...##|#..#.#|#.#..#|##...#|......",
    "й": "......|..##..|#....#|#...##|#..#.#|#.#..#|##...#|......",
    "к": "......|......|#...#.|#..#..|###...|#..#..|#...#.|......",
    "л": "......|......|..####|.#...#|.#...#|.#...#|#....#|......",
    "м": "......|......|#...#.|##.##.|#.#.#.|#...#.|#...#.|......",
    "н": "......|......|#....#|#....#|######|#....#|#....#|......",
    "п": "......|......|######|#....#|#....#|#....#|#....#|......",
    "т": "......|......|#####.|..#...|..#...|..#...|..#...|......",
    "ф": "......|..#...|.###..|#.#.#.|#.#.#.|.###..|..#...|......",
    "ц": "......|......|#...#.|#...#.|#...#.|#...#.|######|.....#",
    "ч": "......|......|#....#|#....#|.#####|.....#|.....#|......",
    "ш": "......|......|#.#.#.|#.#.#.|#.#.#.|#.#.#.|######|......",
    "щ": "......|......|#.#.#.|#.#.#.|#.#.#.|#.#.#.|######|.....#",
    "ь": "......|......|#.....|#.....|#####.|#....#|#####.|......",
    "ю": "......|......|#..##.|#.#..#|###..#|#.#..#|#..##.|......",
    "я": "......|......|.#####|#....#|.#####|..#..#|.#...#|......",
    "ї": ".#.#..|......|.##...|..#...|..#...|..#...|.###..|......",
    "°": ".##...|#..#..|.##...|......|......|......|......|......",
}

_S = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">{}</svg>'
_W = 'fill="none" stroke="white" stroke-linecap="round" stroke-linejoin="round"'
ICONS = {
    "sun": f'<circle cx="8" cy="8" r="3.5" fill="white"/><g {_W} stroke-width="1.5">'
           '<path d="M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.5 1.5M11.5 11.5L13 13M3 13l1.5-1.5M11.5 4.5L13 3"/></g>',
    "cloud": '<path d="M4 13h8a3 3 0 0 0 0-6a4 4 0 0 0-7.5 1A2.5 2.5 0 0 0 4 13z" fill="white"/>',
    "rain": '<path d="M4 10h8a2.5 2.5 0 0 0 0-5a3.5 3.5 0 0 0-6.6 .8A2.2 2.2 0 0 0 4 10z" fill="white"/>'
            f'<g {_W} stroke-width="1.5"><path d="M5 12l-1 2.5M8.5 12l-1 2.5M12 12l-1 2.5"/></g>',
    "snow": f'<g {_W} stroke-width="1.6"><path d="M8 1v14M2 4.5l12 7M2 11.5l12-7"/></g>',
    "heart": '<path d="M8 14.5C2 10.5 1 7 2.5 4.5C4 2 7 2.5 8 4.8C9 2.5 12 2 13.5 4.5C15 7 14 10.5 8 14.5z" fill="white"/>',
    "check": f'<path {_W} stroke-width="2.6" d="M2.5 8.5l3.5 3.5l7.5-8"/>',
    "cross": f'<path {_W} stroke-width="2.6" d="M3.5 3.5l9 9M12.5 3.5l-9 9"/>',
    "bell": '<path d="M8 2a4.5 4.5 0 0 0-4.5 4.5V10L2 12h12l-1.5-2V6.5A4.5 4.5 0 0 0 8 2z" fill="white"/>'
            '<circle cx="8" cy="14" r="1.6" fill="white"/>',
    "clock": f'<circle cx="8" cy="8" r="6.3" {_W} stroke-width="1.8"/><path {_W} stroke-width="1.6" d="M8 4.5V8l2.5 1.5"/>',
    "lamp": '<path d="M8 1.5a4.5 4.5 0 0 0-2.5 8.2V12h5V9.7A4.5 4.5 0 0 0 8 1.5z" fill="white"/>'
            '<rect x="5.5" y="13" width="5" height="1.6" fill="white"/>',
    "lock": '<rect x="3" y="7" width="10" height="8" rx="1" fill="white"/>'
            f'<path {_W} stroke-width="1.8" d="M5 7V5a3 3 0 0 1 6 0v2"/>',
    "home": '<path d="M8 1.5L1.5 7.5H3.5V14.5h3.5v-4h2v4h3.5V7.5h2z" fill="white"/>',
    "battery": f'<rect x="1.5" y="4.5" width="11.5" height="7" rx="1" {_W} stroke-width="1.5"/>'
               '<rect x="3.5" y="6.5" width="7.5" height="3" fill="white"/><rect x="13.5" y="6.5" width="1.5" height="3" fill="white"/>',
    "wifi": f'<g {_W} stroke-width="1.7"><path d="M1.5 6.5a9.5 9.5 0 0 1 13 0M4 9a6 6 0 0 1 8 0M6.3 11.5a2.6 2.6 0 0 1 3.4 0"/></g>'
            '<circle cx="8" cy="14" r="1.3" fill="white"/>',
    "up": f'<path {_W} stroke-width="2.4" d="M8 14V2.5M3 7.5l5-5l5 5"/>',
    "down": f'<path {_W} stroke-width="2.4" d="M8 2v11.5M3 8.5l5 5l5-5"/>',
    "left": f'<path {_W} stroke-width="2.4" d="M14 8H2.5M7.5 3l-5 5l5 5"/>',
    "right": f'<path {_W} stroke-width="2.4" d="M2 8h11.5M8.5 3l5 5l-5 5"/>',
    "mic": '<rect x="5.5" y="1.5" width="5" height="8.5" rx="2.5" fill="white"/>'
           f'<path {_W} stroke-width="1.5" d="M3.5 7.5a4.5 4.5 0 0 0 9 0M8 12v2.5"/>',
    "thermo": '<circle cx="8" cy="12" r="3" fill="white"/><rect x="6.5" y="1.5" width="3" height="10" rx="1.5" fill="white"/>',
    "drop": '<path d="M8 1.5C5 6 3.5 8.3 3.5 10.3a4.5 4.5 0 0 0 9 0C12.5 8.3 11 6 8 1.5z" fill="white"/>',
    "star": '<path d="M8 1.2l2 4.6l5 .4l-3.8 3.3l1.2 4.9L8 11.8l-4.4 2.6l1.2-4.9L1 6.2l5-.4z" fill="white"/>',
    "music": '<path d="M6 12V3.5l7-1.5V10" fill="none" stroke="white" stroke-width="1.6"/>'
             '<circle cx="4.5" cy="12.5" r="2" fill="white"/><circle cx="11.5" cy="10.5" r="2" fill="white"/>',
}


def letter(art: str) -> bytes:
    rows = art.split("|")
    if len(rows) != 8:
        raise ValueError(f"eight rows, not {len(rows)}: {art}")
    out = bytearray()
    for row in rows:
        byte = 0
        for x, char in enumerate(row[:8]):
            if char == "#":
                byte |= 0x80 >> x
        out.append(byte)
    return bytes(out)


def icon(svg: str) -> bytes:
    import resvg_py

    png = resvg_py.svg_to_bytes(svg_string=_S.format(svg), width=16, height=16, background="black")
    rows = _rgba(png)
    out = bytearray()
    for row in rows:
        word = 0
        for x in range(16):
            r, g, b, a = row[x * 4:x * 4 + 4]
            if (r + g + b) / 3 * a / 255 > 110:
                word |= 0x8000 >> x
        out += word.to_bytes(2, "big")
    return bytes(out)


def _rgba(png: bytes) -> list[bytearray]:
    pos, data, width, height = 8, b"", 0, 0
    while pos < len(png):
        size, kind = struct.unpack(">I4s", png[pos:pos + 8])
        body = png[pos + 8:pos + 8 + size]
        pos += 12 + size
        if kind == b"IHDR":
            width, height = struct.unpack(">II", body[:8])
        elif kind == b"IDAT":
            data += body
    raw, stride = zlib.decompress(data), width * 4
    rows, prev = [], bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        kind, line = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        for i in range(stride):
            left = line[i - 4] if i >= 4 else 0
            up, corner = prev[i], prev[i - 4] if i >= 4 else 0
            if kind == 1:
                line[i] = (line[i] + left) & 255
            elif kind == 2:
                line[i] = (line[i] + up) & 255
            elif kind == 3:
                line[i] = (line[i] + (left + up) // 2) & 255
            elif kind == 4:
                guess = left + up - corner
                near = min((abs(guess - left), 0, left), (abs(guess - up), 1, up),
                           (abs(guess - corner), 2, corner))
                line[i] = (line[i] + near[2]) & 255
        rows.append(line)
        prev = line
    return rows


def main() -> int:
    lines = ['"""Generated by scripts/panel_glyphs.py — do not edit by hand.',
             "",
             "LETTERS: Ukrainian letters the board's font lacks, 8 rows of 8 pixels.",
             "WIDE: letters that take seven pixels across, not six.",
             "LOOKALIKE: letters drawn with the board's Latin ones.",
             "ICONS: 16×16, two bytes a row. Rows top to bottom, leftmost the highest bit.",
             '"""', "", "LETTERS = {"]
    for char, art in LETTERS.items():
        lines.append(f"    {char!r}: {letter(art)!r},")
    lines += ["}", "", "WIDE = " + repr("".join(c for c, a in LETTERS.items() if len(a.split('|')[0]) > 6)),
              "", "LOOKALIKE = {"]
    for char, latin in LOOKALIKE.items():
        lines.append(f"    {char!r}: {latin!r},")
    lines += ["}", "", "ICONS = {"]
    for name, svg in ICONS.items():
        lines.append(f"    {name!r}: {icon(svg)!r},")
    lines += ["}", ""]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"{OUT}: {len(LETTERS)} letters, {len(ICONS)} icons")
    return 0


if __name__ == "__main__":
    sys.exit(main())
