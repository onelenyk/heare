"""What can stand in a box of the screen, and how each is drawn.

Every widget is two functions: ``draw`` puts it on the canvas inside its
box, and ``key`` names what it looks like now — the screen is drawn again
only when a key changes, which is how an animation asks for its next frame
and a still widget asks for none.

A widget reads its values from the scene's fields: ``"bind": "temp"`` takes
``fields["temp"]``, a value in the widget itself is the default.

``state`` is the face and the status words, as the panel has always drawn
them; it fills the whole window itself. Pure: imports only the panel's own
drawing modules, the same on the board and on a laptop.
"""

from heare_panel import face, font, glyphs, render

BLINK_MS = 500
FACE_STATES = ("IDLE", "HEAR", "THINK", "SPEAK")
# Widgets that paint the whole window themselves; the others are drawn on
# a cleared one.
FILLS = ("state",)
KINDS = ("state", "text", "picture", "number", "icon", "bar", "clock", "spinner")
SPIN_MS = 125
ICON = 16


def value(widget, fields, name="value", default=None):
    bound = widget.get("bind")
    if bound is not None and bound in fields:
        return fields[bound]
    return widget.get(name, default)


def _shows_face(word, box):
    return word in FACE_STATES and box[2:] == (72, 40)


def draw(widget, canvas, box, fields, now_ms):
    kind = widget.get("widget")
    if kind == "state":
        word = value(widget, fields, "word", "BOOT")
        if _shows_face(word, box):
            face.draw(word, canvas, now_ms)
        else:
            render.render(word, canvas, box[2], box[3], now_ms)
    elif kind == "text":
        lines = value(widget, fields, "lines", None)
        if lines is not None:
            _text(canvas, box, lines, widget.get("at"))
        else:
            _words(canvas, box, str(value(widget, fields, "text", "")),
                   _size(widget), widget.get("align", "center"))
    elif kind == "picture":
        _picture(canvas, box, value(widget, fields, "bits", b""))
    elif kind == "number":
        _number(canvas, box, value(widget, fields, "value", ""), widget.get("unit", ""))
    elif kind == "icon":
        _icon(canvas, box, value(widget, fields, "name", ""), value(widget, fields, "bits", None))
    elif kind == "bar":
        _bar(canvas, box, value(widget, fields, "value", 0))
    elif kind == "clock":
        _clock(canvas, box, now_ms, value(widget, fields, "tz", 0))
    elif kind == "spinner":
        _spinner(canvas, box, now_ms)


def key(widget, fields, now_ms, box):
    kind = widget.get("widget")
    if kind == "state":
        word = value(widget, fields, "word", "BOOT")
        if _shows_face(word, box):
            return face.frame_key(word, now_ms)
        if word == "ALLOW":
            return (word, now_ms // BLINK_MS)
        return (word,)
    if kind == "clock":
        stamp = _local(value(widget, fields, "tz", 0))
        return (stamp[3], stamp[4], stamp[5] % 2)
    if kind == "spinner":
        return now_ms // SPIN_MS
    return None


def _size(widget):
    return 2 if widget.get("size") == 2 else 1


def wrap(text, per_line, at_most):
    """Words into lines of ``per_line`` letters; a word too long is cut."""
    lines, line = [], ""
    for word in font.plain(text).split():
        while len(word) > per_line:
            if line:
                lines.append(line)
                line = ""
            lines.append(word[:per_line])
            word = word[per_line:]
        if line and len(line) + 1 + len(word) > per_line:
            lines.append(line)
            line = ""
        line = line + " " + word if line else word
    if line:
        lines.append(line)
    return lines[:at_most]


def _fits(text, size, width, height):
    """Whether every word stands whole on its line and the lines fit."""
    per_line = max(width // (font.CELL * size), 1)
    rows = max((height + 2 * size) // (10 * size), 1)
    words = font.plain(text).split()
    if any(len(word) > per_line for word in words):
        return False
    return len(wrap(text, per_line, rows + 1)) <= rows


def _words(canvas, box, text, size, align):
    x, y, width, height = box
    # Big only when it fits whole: a word is never broken to stay big.
    if size == 2 and not _fits(text, 2, width, height):
        size = 1
    step = font.CELL * size
    lines = wrap(text, max(width // step, 1), max((height + 2 * size) // (10 * size), 1))
    top = y + max((height - len(lines) * 10 * size + 2 * size) // 2, 0)
    for index, line in enumerate(lines):
        left = x if align == "left" else x + max((width - len(line) * step) // 2, 0)
        font.draw(canvas, line, left, top + index * 10 * size, size)


def _number(canvas, box, number, unit):
    x, y, width, height = box
    text = str(number) + str(unit)
    size = 2 if font.width(text, 2) <= width and height >= 16 else 1
    font.draw(canvas, text, x + max((width - font.width(text, size)) // 2, 0),
              y + max((height - font.CELL * size) // 2, 0), size)


def _icon(canvas, box, name, bits):
    x, y, width, height = box
    data = glyphs.ICONS.get(name) if bits is None else bits
    if not data:
        return
    # Named or drawn from an SVG, an icon is 16×16: the hands draw it so.
    side = ICON
    left, top = x + max((width - side) // 2, 0), y + max((height - side) // 2, 0)
    stride = (side + 7) // 8
    for row in range(side):
        for column in range(side):
            byte = data[row * stride + column // 8] if row * stride + column // 8 < len(data) else 0
            if byte & (0x80 >> (column % 8)):
                canvas.pixel(left + column, top + row, 1)


def _bar(canvas, box, percent):
    x, y, width, height = box
    try:
        percent = max(0, min(int(percent), 100))
    except (TypeError, ValueError):
        percent = 0
    tall = min(height, 8)
    top = y + (height - tall) // 2
    canvas.rect(x, top, width, tall, 1)
    inner = (width - 4) * percent // 100
    if inner > 0:
        canvas.fill_rect(x + 2, top + 2, inner, tall - 4, 1)


def _local(tz_minutes):
    import time

    try:
        shift = int(tz_minutes) * 60
    except (TypeError, ValueError):
        shift = 0
    return time.localtime(time.time() + shift)


def _clock(canvas, box, now_ms, tz_minutes):
    stamp = _local(tz_minutes)
    colon = ":" if stamp[5] % 2 == 0 else " "
    text = "%02d%s%02d" % (stamp[3], colon, stamp[4])
    x, y, width, height = box
    size = 2 if font.width(text, 2) <= width and height >= 16 else 1
    font.draw(canvas, text, x + max((width - font.width(text, size)) // 2, 0),
              y + max((height - font.CELL * size) // 2, 0), size)


def _spinner(canvas, box, now_ms):
    x, y, width, height = box
    cx, cy = x + width // 2, y + height // 2
    radius = max(min(width, height) // 2 - 3, 3)
    lit = (now_ms // SPIN_MS) % 8
    # Eight dots around a circle; the lit one and its tail are larger.
    points = ((0, -10), (7, -7), (10, 0), (7, 7), (0, 10), (-7, 7), (-10, 0), (-7, -7))
    for index, (dx, dy) in enumerate(points):
        px, py = cx + dx * radius // 10, cy + dy * radius // 10
        near = (lit - index) % 8
        side = 3 if near == 0 else 2 if near == 1 else 1
        canvas.fill_rect(px - side // 2, py - side // 2, side, side, 1)


def _text(canvas, box, lines, at=None):
    x, y, width, _height = box
    for index, line in enumerate(lines):
        top = at[index] if at and index < len(at) else index * 10
        canvas.text(line, x + max((width - len(line) * 8) // 2, 0), y + top, 1)


def _picture(canvas, box, bits, source=(72, 40)):
    x, y, width, height = box
    if (width, height) == source:
        stride = width // 8
        for row in range(height):
            for column in range(stride):
                byte = bits[row * stride + column] if row * stride + column < len(bits) else 0
                if not byte:
                    continue
                for bit in range(8):
                    if byte & (0x80 >> bit):
                        canvas.pixel(x + column * 8 + bit, y + row, 1)
        return
    # A whole-window picture in a smaller box: every box pixel takes the
    # source pixel under it, keeping the picture's proportions.
    src_w, src_h = source
    scale = max(src_w * 1000 // max(width, 1), src_h * 1000 // max(height, 1))
    shown_w, shown_h = src_w * 1000 // scale, src_h * 1000 // scale
    left, top = x + (width - shown_w) // 2, y + (height - shown_h) // 2
    stride = src_w // 8
    for row in range(shown_h):
        sy = row * scale // 1000
        for column in range(shown_w):
            sx = column * scale // 1000
            index = sy * stride + sx // 8
            if index < len(bits) and bits[index] & (0x80 >> (sx % 8)):
                canvas.pixel(left + column, top + row, 1)
