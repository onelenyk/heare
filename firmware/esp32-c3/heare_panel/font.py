"""Text on the window, Ukrainian included, at size 1 or 2.

The board's font is Latin only and drawn by ``canvas.text``. A Ukrainian
letter that looks like a Latin one is drawn with it; the rest come from
``glyphs.letter`` and are drawn pixel by pixel. Size 2 is every letter's
eight rows doubled: the board's own letters are read back from a scratch
``framebuf`` once each (``rows``), which a laptop has not — tests hand in
their own (``ascii_rows``).

Every letter is eight pixels across at size 1, as the board's font is, so
a line's width is always ``len(text) * 8 * size``.
"""

from heare_panel import glyphs

CELL = 8
_cache = {}
# How the board's own letter is read back as eight rows; replaced in tests.
ascii_rows = None


def plain(text):
    """The text in the letters there are: lookalikes, and ? for the rest."""
    out = []
    for char in str(text):
        char = glyphs.look(char)
        if glyphs.letter(char) is not None or " " <= char <= "~":
            out.append(char)
        else:
            out.append("?")
    return "".join(out)


def width(text, size=1):
    return len(plain(text)) * CELL * size


def draw(canvas, text, x, y, size=1):
    """Draw the text with its top left at (x, y)."""
    text = plain(text)
    if size == 1:
        run_start, run = x, ""
        for index, char in enumerate(text):
            own = glyphs.letter(char)
            if own is not None:
                if run:
                    canvas.text(run, run_start, y, 1)
                _letter(canvas, own, x + index * CELL, y, 1)
                run, run_start = "", x + (index + 1) * CELL
            else:
                run += char
        if run:
            canvas.text(run, run_start, y, 1)
        return
    for index, char in enumerate(text):
        if char == " ":
            continue
        found = glyphs.letter(char) or rows(char)
        if found:
            _letter(canvas, found, x + index * CELL * size, y, size)


def rows(char):
    """The board font's letter as eight rows, leftmost the highest bit."""
    if char in _cache:
        return _cache[char]
    found = None
    if ascii_rows is not None:
        found = ascii_rows(char)
    else:
        try:
            import framebuf

            scratch = bytearray(CELL)
            framebuf.FrameBuffer(scratch, CELL, CELL, framebuf.MONO_HLSB).text(char, 0, 0, 1)
            found = bytes(scratch)
        except ImportError:
            found = None
    _cache[char] = found
    return found


def _letter(canvas, letter, x, y, size):
    for row in range(CELL):
        bits = letter[row]
        if not bits:
            continue
        for column in range(CELL):
            if bits & (0x80 >> column):
                if size == 1:
                    canvas.pixel(x + column, y + row, 1)
                else:
                    canvas.fill_rect(x + column * size, y + row * size, size, size, 1)
