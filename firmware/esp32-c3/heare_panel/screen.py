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
# The layers the hands may put a scene on, by the names they use.
AGENT_LAYERS = {"flash": "flash", "pin": "pinned", "idle": "idle"}
MAX_WIDGETS = 8
MAX_FIELDS = 16
MAX_BITMAPS = 16
MAX_FRAMES = 8
COUNT_MS = 800
_made = [0]


class SceneError(ValueError):
    """A scene that cannot be shown, in words a model can act on."""


class Scene:
    """A layout of widgets and the fields they show."""

    def __init__(self, name, spec, fields=None):
        self.name = name
        self.spec = spec
        self.fields = dict(fields or {})
        self.version = 0
        self.moving = {}
        # Two scenes of one name are still two pictures.
        _made[0] += 1
        self.serial = _made[0]

    def update(self, fields, now_ms=None):
        changed = False
        for name in fields:
            old = self.fields.get(name)
            if old != fields[name]:
                if now_ms is not None and _number(old) and _number(fields[name]):
                    # A number moves to its new value, it does not jump.
                    self.moving[name] = (self.shown(now_ms).get(name, old), now_ms)
                self.fields[name] = fields[name]
                changed = True
        if changed:
            self.version += 1
        return changed

    def shown(self, now_ms):
        """The fields as seen now: a number that changed is on its way."""
        if not self.moving:
            return self.fields
        found = dict(self.fields)
        for name, (start, at) in list(self.moving.items()):
            gone = _elapsed(at, now_ms)
            if gone >= COUNT_MS or gone < 0:
                del self.moving[name]
                continue
            end = self.fields[name]
            value = start + (end - start) * gone / COUNT_MS
            found[name] = int(round(value)) if isinstance(end, int) else round(value, 1)
        return found

    def placed(self, width, height):
        return layout.boxes(self.spec, 0, 0, width, height)

    def key(self, width, height, now_ms):
        fields = self.shown(now_ms)
        found = [self.name, self.serial, self.version]
        if self.moving:
            found.append(tuple(sorted((k, fields[k]) for k in self.moving)))
        for box, widget in self.placed(width, height):
            found.append(widgets.key(widget, fields, now_ms, box))
        return tuple(found)

    def draw(self, canvas, width, height, now_ms):
        placed = self.placed(width, height)
        fields = self.shown(now_ms)
        if not any(widget.get("widget") in widgets.FILLS for _box, widget in placed):
            canvas.fill(0)
        for box, widget in placed:
            widgets.draw(widget, canvas, box, fields, now_ms)


def check(spec, width=72, height=40):
    """A scene from the hands: laid out, of known widgets, within bounds.

    The pictures in it come as base64 ``bits`` (the hands drew the SVG) and
    are decoded here, once."""
    if not isinstance(spec, dict):
        raise SceneError("сцена — це об'єкт {id, layout, fields}")
    name = spec.get("id") or "scene"
    if not isinstance(name, str) or not name or len(name) > 32:
        raise SceneError("id сцени — короткий рядок")
    fields = spec.get("fields") or {}
    if not isinstance(fields, dict) or len(fields) > MAX_FIELDS:
        raise SceneError("fields — об'єкт, до " + str(MAX_FIELDS) + " полів")
    tidy = layout.tidy(spec.get("layout"))
    try:
        placed = layout.boxes(tidy, 0, 0, width, height)
    except layout.LayoutError as error:
        raise SceneError(str(error)) from None
    if len(placed) > MAX_WIDGETS:
        raise SceneError("забагато віджетів: до " + str(MAX_WIDGETS))
    pictures = 0
    for _box, widget in placed:
        kind = widget.get("widget")
        if kind not in widgets.KINDS or kind == "state":
            raise SceneError("невідомий віджет: " + str(kind) + "; є " + ", ".join(
                k for k in widgets.KINDS if k != "state"))
        wanted = width * height // 8 if kind == "picture" else 32
        if "bits" in widget:
            widget["bits"] = _bits(widget["bits"])
            if len(widget["bits"]) != wanted:
                raise SceneError("картинка не того розміру: " + kind + " — "
                                 + str(wanted) + " байт")
            pictures += 1
        if "frames" in widget:
            frames = widget["frames"]
            if not isinstance(frames, list) or not 2 <= len(frames) <= MAX_FRAMES:
                raise SceneError("frames — від 2 до " + str(MAX_FRAMES) + " кадрів")
            widget["frames"] = [_bits(frame) for frame in frames]
            if any(len(frame) != wanted for frame in widget["frames"]):
                raise SceneError("кадр не того розміру: " + kind + " — " + str(wanted) + " байт")
            pictures += len(frames)
        if widget.get("anim") not in (None, "blink", "scroll", "bounce"):
            raise SceneError("anim: blink, scroll або bounce")
    if pictures > MAX_BITMAPS:
        raise SceneError("забагато картинок: до " + str(MAX_BITMAPS))
    return Scene(name, tidy, fields)


def _bits(encoded):
    import binascii

    if isinstance(encoded, bytes):
        return encoded
    try:
        return binascii.a2b_base64(encoded or "")
    except (ValueError, TypeError):
        raise SceneError("картинка не base64") from None


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

    def find(self, name):
        """The hands' scene of that id, on whichever layer it is."""
        for layer in ("flash", "pinned", "idle"):
            scene = self._scenes.get(layer)
            if scene is not None and scene.name == name:
                return scene
        return None

    def layers(self):
        """What is on each layer now, by scene id."""
        return {layer: scene.name for layer, scene in self._scenes.items()}

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


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _elapsed(start, now):
    return ((now - start + 0x20000000) & 0x3FFFFFFF) - 0x20000000


def _after(now_ms, until_ms):
    # The board's ticks wrap; a difference read as signed 30 bits does not.
    return ((now_ms - until_ms) & 0x3FFFFFFF) < 0x20000000
