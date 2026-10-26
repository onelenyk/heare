"""The screen: scenes on layers, and which one is seen.

Everything on the panel's window is a scene — a layout of widgets and the
fields they show. The face is one, the status words are one, the words and
pictures the hands send are scenes too. Which is seen is the layers':

    system   ALLOW: a permission is never hidden
    flash    from the hands, for some seconds
    pinned   from the hands, until taken down
    idle     the screen at rest, instead of the face
    face     the face and the status words

The highest layer with a scene is seen; a scene on ``flash`` or ``pinned``
that has run out of time leaves, and the one below shows again. The main
loop only asks ``key`` each tick and calls ``draw`` when the key changed.

Pure: the same on the board and on a laptop.
"""

from heare_panel import layout, widgets

LAYERS = ("system", "flash", "pinned", "idle", "face")
_made = [0]


class Scene:
    """A layout of widgets and the fields they show."""

    def __init__(self, name, spec, fields=None):
        self.name = name
        self.spec = spec
        self.fields = dict(fields or {})
        self.version = 0
        # Two scenes of one name are still two pictures.
        _made[0] += 1
        self.serial = _made[0]

    def update(self, fields):
        changed = False
        for name in fields:
            if self.fields.get(name) != fields[name]:
                self.fields[name] = fields[name]
                changed = True
        if changed:
            self.version += 1
        return changed

    def placed(self, width, height):
        return layout.boxes(self.spec, 0, 0, width, height)

    def key(self, width, height, now_ms):
        found = [self.name, self.serial, self.version]
        for box, widget in self.placed(width, height):
            found.append(widgets.key(widget, self.fields, now_ms, box))
        return tuple(found)

    def draw(self, canvas, width, height, now_ms):
        placed = self.placed(width, height)
        if not any(widget.get("widget") in widgets.FILLS for _box, widget in placed):
            canvas.fill(0)
        for box, widget in placed:
            widgets.draw(widget, canvas, box, self.fields, now_ms)


def state_scene(word="BOOT"):
    return Scene("state", {"widget": "state", "bind": "word"}, {"word": word})


def text_scene(lines):
    return Scene("text", {"widget": "text", "bind": "lines"}, {"lines": list(lines)})


def picture_scene(bits):
    return Scene("picture", {"widget": "picture", "bind": "bits"}, {"bits": bits})


class Screen:
    """The layers of one window."""

    def __init__(self, width=72, height=40):
        self.width = width
        self.height = height
        self._scenes = {}
        self._until = {}
        self.face = state_scene()
        self._scenes["face"] = self.face

    def put(self, layer, scene, until_ms=None):
        if layer not in LAYERS:
            raise ValueError("no layer " + str(layer))
        self._scenes[layer] = scene
        if until_ms is None:
            self._until.pop(layer, None)
        else:
            self._until[layer] = until_ms

    def clear(self, layer):
        if layer != "face":
            self._scenes.pop(layer, None)
            self._until.pop(layer, None)

    def scene(self, layer):
        return self._scenes.get(layer)

    def word(self, word):
        """What heare is doing: the face's, and ALLOW above everything."""
        self.face.update({"word": word})
        if word == "ALLOW":
            if self.scene("system") is None:
                self.put("system", state_scene("ALLOW"))
        else:
            self.clear("system")

    def seen(self, now_ms):
        """The layer and the scene that are seen now."""
        for layer in LAYERS:
            scene = self._scenes.get(layer)
            if scene is None:
                continue
            until = self._until.get(layer)
            if until is not None and _after(now_ms, until):
                self.clear(layer)
                continue
            return layer, scene
        return "face", self.face

    def key(self, now_ms):
        layer, scene = self.seen(now_ms)
        return (layer,) + scene.key(self.width, self.height, now_ms)

    def draw(self, canvas, width, height, now_ms):
        """Draw what is seen; a Screen paints like a Scene."""
        _layer, scene = self.seen(now_ms)
        scene.draw(canvas, width, height, now_ms)


def _after(now_ms, until_ms):
    # The board's ticks wrap; a difference read as signed 30 bits does not.
    return ((now_ms - until_ms) & 0x3FFFFFFF) < 0x20000000
