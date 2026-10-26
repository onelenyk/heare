"""An SVG the model wrote, as the pixels a part's screen takes.

A part with a screen says in a skill's ``render`` what it wants a parameter
turned into — ``{"to": "mono", "width": 72, "height": 40, "as": "bitmap"}`` —
and the hands do it before the call: the board on the desk cannot draw an
SVG, the Mac can in a few milliseconds. The hands know no screens; the size
and the form are the part's.

``mono`` is one bit a pixel, white on black: rows top to bottom, eight
pixels a byte, the leftmost the highest bit (MicroPython's ``MONO_HLSB``),
in base64.

Nothing an SVG names is fetched: images, scripts, foreign objects and any
link that leaves the document are cut out before it is drawn.
"""

from __future__ import annotations

import base64
import re
import struct
import zlib

FORMS = ("mono",)
MAX_SIDE = 256
MAX_SVG = 20_000

_TAGS = r"(?:script|image|foreignObject|iframe|use|style)"
# Whole blocks first (an element and all it holds), then any of their tags left.
_BLOCK = re.compile(rf"<\s*({_TAGS})\b[^>]*(?<!/)>.*?<\s*/\s*\1\s*>", re.I | re.S)
_TAG = re.compile(rf"<\s*/?\s*{_TAGS}\b[^>]*>", re.I | re.S)
_OUTSIDE = re.compile(r"""\s(?:xlink:)?href\s*=\s*(["'])(?!#).*?\1""", re.I | re.S)
_EVENT = re.compile(r"""\son\w+\s*=\s*(["']).*?\1""", re.I | re.S)


class Unfit(ValueError):
    """What came cannot be drawn, in words the model can act on."""


def clean(svg: str) -> str:
    """The SVG with nothing in it that reaches outside the document."""
    if len(svg) > MAX_SVG:
        raise Unfit(f"SVG задовгий: до {MAX_SVG} символів")
    found = _TAG.sub("", _BLOCK.sub("", svg))
    found = _OUTSIDE.sub("", found)
    return _EVENT.sub("", found)


def render(svg: str, spec: dict) -> str:
    """The SVG drawn at the part's size, in the part's form, as base64."""
    import resvg_py

    width, height = int(spec["width"]), int(spec["height"])
    try:
        png = resvg_py.svg_to_bytes(svg_string=clean(svg), width=width, height=height,
                                    background="black")
    except ValueError as exc:
        raise Unfit(f"SVG не малюється: {exc}") from None
    w, h, rows = _rgba(png)
    lit = [[_bright(row, x) for x in range(w)] for row in rows]
    if not any(any(line) for line in lit):
        raise Unfit("картинка вийшла порожня: малюй білим по чорному, товщими лініями")
    if all(all(line) for line in lit):
        raise Unfit("картинка вийшла суцільно білою: фон має бути чорним")
    return base64.b64encode(mono(lit)).decode()


def mono(lit: list[list[bool]]) -> bytes:
    """Rows of lit pixels, eight a byte, the leftmost the highest bit."""
    out = bytearray()
    for line in lit:
        for start in range(0, len(line), 8):
            byte = 0
            for offset, on in enumerate(line[start:start + 8]):
                if on:
                    byte |= 0x80 >> offset
            out.append(byte)
    return bytes(out)


def _bright(row: bytearray, x: int) -> bool:
    r, g, b, a = row[x * 4:x * 4 + 4]
    return (r + g + b) / 3 * a / 255 > 127


def _rgba(png: bytes) -> tuple[int, int, list[bytearray]]:
    """A PNG of 8-bit RGBA, as resvg writes it, into rows of pixels."""
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise Unfit("не PNG")
    pos, data, width, height = 8, b"", 0, 0
    while pos < len(png):
        size, kind = struct.unpack(">I4s", png[pos:pos + 8])
        body = png[pos + 8:pos + 8 + size]
        pos += 12 + size
        if kind == b"IHDR":
            width, height, depth, color = struct.unpack(">IIBB", body[:10])
            if (depth, color, body[12]) != (8, 6, 0):
                raise Unfit("PNG не того виду")
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
    return width, height, rows
