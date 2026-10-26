"""A scene as the panel would draw it, as a PNG — before it goes to the board.

    make panel-preview SCENE='{"rows": [...]}' OUT=scene.png

The firmware's own engine draws it (layouts, widgets, the Ukrainian
letters); only the board's Latin letters are stood in for by a monospace
font drawn at 8 px, so Latin text looks near, not exact.
"""

from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "firmware" / "esp32-c3"), str(ROOT)]

from heare_panel import font, screen  # noqa: E402

from tests.firmware.raster import Raster  # noqa: E402

SCALE = 8


def latin(char: str) -> bytes | None:
    """A Latin letter at 8×8, drawn by resvg in a monospace font."""
    import resvg_py

    if char == " ":
        return bytes(8)
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8">'
           f'<text x="0.5" y="7" font-family="Menlo, monospace" font-size="8.5" '
           f'fill="white">{char.replace("&", "&amp;").replace("<", "&lt;")}</text></svg>')
    png = resvg_py.svg_to_bytes(svg_string=svg, width=8, height=8, background="black")
    from heare.pixels import _rgba  # the hands' own PNG reader

    _w, _h, rows = _rgba(png)
    return bytes(sum(0x80 >> x for x in range(8)
                     if (row[x * 4] + row[x * 4 + 1] + row[x * 4 + 2]) / 3 > 100) for row in rows)


def png(raster: Raster, path: Path) -> None:
    rows = []
    for line in raster.lit:
        row = bytearray([0])
        for on in line:
            row += bytes([255 if on else 20]) * SCALE
        rows += [bytes(row)] * SCALE

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", raster.width * SCALE,
                                                  raster.height * SCALE, 8, 0, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


def main() -> int:
    spec = json.loads(sys.argv[1])
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "scene.png")
    font.ascii_rows = latin
    raster = Raster(letters=latin)
    scene = screen.Scene("preview", spec.get("layout", spec), spec.get("fields"))
    scene.draw(raster, 72, 40, 0)
    png(raster, out)
    print(raster.ascii())
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
