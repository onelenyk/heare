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

from heare_panel import face, render

BLINK_MS = 500
FACE_STATES = ("IDLE", "HEAR", "THINK", "SPEAK")
# Widgets that paint the whole window themselves; the others are drawn on
# a cleared one.
FILLS = ("state",)


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
        _text(canvas, box, value(widget, fields, "lines", []), widget.get("at"))
    elif kind == "picture":
        _picture(canvas, box, value(widget, fields, "bits", b""))


def key(widget, fields, now_ms, box):
    kind = widget.get("widget")
    if kind == "state":
        word = value(widget, fields, "word", "BOOT")
        if _shows_face(word, box):
            return face.frame_key(word, now_ms)
        if word == "ALLOW":
            return (word, now_ms // BLINK_MS)
        return (word,)
    return None


def _text(canvas, box, lines, at=None):
    x, y, width, _height = box
    for index, line in enumerate(lines):
        top = at[index] if at and index < len(at) else index * 10
        canvas.text(line, x + max((width - len(line) * 8) // 2, 0), y + top, 1)


def _picture(canvas, box, bits):
    x, y, width, height = box
    stride = width // 8
    for row in range(height):
        for column in range(stride):
            byte = bits[row * stride + column] if row * stride + column < len(bits) else 0
            if not byte:
                continue
            for bit in range(8):
                if byte & (0x80 >> bit):
                    canvas.pixel(x + column * 8 + bit, y + row, 1)
