"""What the hands may do with the board: words on the screen, the light, pins.

The panel brings its skills in the knock, in its own words: a name, a line
about it, its parameters and its effect. The brain only checks their form and
delivers a ``call`` for a skill named here; the hands decide by the effect
which wait for the person's yes (the pins). This module checks again anyway:
a name or a value it does not know is refused, never guessed at, and every
call is answered — done, with what came of it, or not, with why.

Only the pins nothing on the board uses are offered. Not the screen (5, 6),
the BOOT button (9), the light (8), the flash (12-17) or USB (18, 19): a
level driven onto any of those hangs the board or worse. Every offered pin
starts as an input with a pull-down, so an empty pin reads 0 rather than
noise, and becomes an output only once it is switched. A reboot leaves all
of them inputs again: nothing the agent switched outlives a restart.

The pin factory is injected (``machine.Pin`` on the board, a fake in
``tests/firmware``), and so is the clock.
"""

LED_PIN = 8
IDLE_PATH = "/idle.json"
VIEW_W = 72
VIEW_H = 40
PICTURE_BYTES = VIEW_W * VIEW_H // 8
FREE_PINS = (0, 1, 2, 3, 4, 7, 10, 20, 21)
LINE_CHARS = 9
MAX_LINES = 4
SHOW_MAX_MS = 60_000
BLINK_MS = 400


# The skills travel in every knock, and the board has some 66 KB: words in
# them are English and short (a model reads either), and the pins are one
# skill, not nine.
SCENE_HELP = (
    "Example: {\"id\":\"weather\",\"fields\":{\"t\":21},\"layout\":{\"cols\":["
    "{\"widget\":\"icon\",\"name\":\"sun\"},{\"w\":2,\"widget\":\"number\","
    "\"bind\":\"t\",\"unit\":\"°\"}]}}. Every widget is {\"widget\":KIND,...}; "
    "rows:[..] stack, cols:[..] side by side, weights h/w 1-12, 8 widgets at most. "
    "KIND: text{text,size 1|2,align}, number{value,unit}, icon{name} or icon{svg 16x16}, "
    "picture{svg 72x40 white on black}, bar{value 0-100}, clock, spinner. "
    "bind:'t' shows fields.t; update changes fields, numbers move there smoothly. "
    "Any widget: anim blink|scroll(text)|bounce. picture/icon: frames:[svg,..] 2-8, "
    "every ms, plays them. Icons: sun cloud rain snow heart "
    "check cross bell clock lamp lock home battery wifi up down left right mic thermo "
    "drop star music."
)
_OBJECT = {"type": "object", "properties": {}}
_LAYER = {"type": "string", "enum": ["flash", "pin", "idle"]}


def skills():
    """The skills the knock offers the hands."""
    return [
        {"name": "show", "effect": "harmless", "about": "Show a few words on the OLED for some seconds",
         "params": {"type": "object", "required": ["text"], "properties": {
             "text": {"type": "string"}, "seconds": {"type": "integer"}}}},
        {"name": "image", "effect": "harmless", "about": "Draw a picture on the 72x40 OLED for some seconds",
         "params": {"type": "object", "required": ["svg"], "properties": {
             "svg": {"type": "string", "description": "viewBox 0 0 72 40, white on black, thick lines"},
             "seconds": {"type": "integer"}}},
         "render": {"svg": {"to": "mono", "width": VIEW_W, "height": VIEW_H, "as": "bitmap"}}},
        {"name": "screen", "effect": "harmless",
         "about": "Put a scene on the OLED: flash for seconds, pin until cleared, idle instead of the face",
         "params": {"type": "object", "required": ["scene"], "properties": {
             "scene": {"type": "object", "description": SCENE_HELP},
             "layer": _LAYER, "seconds": {"type": "integer"}}},
         "render": {"scene": [
             {"deep": True, "key": "svg", "where": {"widget": "icon"},
              "to": "mono", "width": 16, "height": 16, "as": "bits"},
             {"deep": True, "key": "svg", "where": {"widget": "picture"},
              "to": "mono", "width": VIEW_W, "height": VIEW_H, "as": "bits"},
             {"deep": True, "key": "frames", "where": {"widget": "icon"},
              "to": "mono", "width": 16, "height": 16, "as": "frames"},
             {"deep": True, "key": "frames", "where": {"widget": "picture"},
              "to": "mono", "width": VIEW_W, "height": VIEW_H, "as": "frames"}]}},
        {"name": "update", "effect": "harmless", "about": "Change fields of a scene on the screen",
         "params": {"type": "object", "required": ["id", "fields"], "properties": {
             "id": {"type": "string"}, "fields": _OBJECT}}},
        {"name": "clear", "effect": "harmless", "about": "Take a scene (id) or a layer off the screen",
         "params": {"type": "object", "properties": {"id": {"type": "string"}, "layer": _LAYER}}},
        {"name": "screen_info", "effect": "read", "about": "What is on the screen, its size, widgets, icons",
         "params": _OBJECT},
        {"name": "log", "effect": "read", "about": "The board's last log lines", "params": _OBJECT},
        {"name": "light", "effect": "harmless", "about": "The board's LED: on, off or blink (blinks by itself)",
         "params": {"type": "object", "required": ["mode"], "properties": {
             "mode": {"type": "string", "enum": ["on", "off", "blink"]}}}},
        {"name": "read_pins", "effect": "read", "about": "Levels of the free pins, 1 or 0",
         "params": _OBJECT},
        {"name": "pin", "effect": "change", "about": "Switch a free pin on or off: whatever is wired to it",
         "params": {"type": "object", "required": ["n", "on"], "properties": {
             "n": {"type": "integer", "enum": list(FREE_PINS)}, "on": {"type": "boolean"}}}},
    ]


def lines(text):
    """At most four lines of nine characters: what the 72x40 window holds."""
    found = []
    for part in str(text).split("\n"):
        line = ""
        for word in part.split():
            while len(word) > LINE_CHARS:
                if line:
                    found.append(line)
                    line = ""
                found.append(word[:LINE_CHARS])
                word = word[LINE_CHARS:]
            if line and len(line) + 1 + len(word) > LINE_CHARS:
                found.append(line)
                line = ""
            line = line + " " + word if line else word
        if line:
            found.append(line)
    return found[:MAX_LINES]


class Device:
    """The board's acts. ``apply`` is called from the link's thread; the
    display loop reads ``shown`` and calls ``tick``. Plain attribute writes
    are all they share."""

    def __init__(self, pin_factory=None, screen=None):
        from heare_panel import screen as screen_module

        self._make = pin_factory
        self.screen = screen if screen is not None else screen_module.Screen()
        self.idle_path = IDLE_PATH
        self._pins = {}
        self._outputs = {}
        self._led = None
        self.led_mode = "off"

    def apply(self, name, value, now_ms):
        """Do one act; False for a name or a value this panel does not know."""
        if name == "show":
            if not isinstance(value, dict):
                return False
            shown = lines(value.get("text") or "")
            try:
                seconds = int(value.get("seconds") or 10)
            except (TypeError, ValueError):
                return False
            if not shown:
                return False
            hold = max(1000, min(seconds * 1000, SHOW_MAX_MS))
            self.screen.put("flash", _scenes().text_scene(shown), now_ms + hold)
            return True
        if name == "led":
            if value not in ("on", "off", "blink"):
                return False
            self.led_mode = value
            return True
        number = _pin_number(name)
        if number is None or not isinstance(value, bool):
            return False
        pin = self._output(number)
        if pin is None:
            return False
        pin.value(1 if value else 0)
        return True

    def call(self, name, args, now_ms):
        """One skill as the hands call it: (ok, what came of it)."""
        if not isinstance(args, dict):
            return False, "аргументи не ті"
        if name == "show":
            text = str(args.get("text") or "")
            done = self.apply("show", {"text": text, "seconds": args.get("seconds") or 10}, now_ms)
            return (True, "показано: " + " ".join(lines(text))) if done else (False, "нема що показати")
        if name == "image":
            return self._picture(args.get("bitmap"), args.get("seconds"), now_ms)
        if name == "screen":
            return self._screen(args, now_ms)
        if name == "update":
            scene = self.screen.find(str(args.get("id") or ""))
            if scene is None:
                return False, "такої сцени на екрані немає"
            if not isinstance(args.get("fields"), dict):
                return False, "fields — об'єкт"
            scene.update(args["fields"], now_ms)
            return True, "оновлено: " + scene.name
        if name == "clear":
            return self._clear(args)
        if name == "screen_info":
            return True, self._info()
        if name == "log":
            from heare_panel import journal

            return True, journal.lines()
        if name == "light":
            done = self.apply("led", args.get("mode"), now_ms)
            return (True, "світло: " + self.led_mode) if done else (False, "on, off або blink")
        if name == "read_pins":
            return True, self.levels()
        if name == "pin":
            on, number = args.get("on"), args.get("n")
            if not isinstance(on, bool):
                return False, "on: true або false"
            if number not in FREE_PINS:
                return False, "пін недоступний"
            self.apply("pin" + str(number), on, now_ms)
            return True, "pin" + str(number) + ": " + ("1" if on else "0")
        return False, "такого скіла немає"

    def _screen(self, args, now_ms):
        from heare_panel import screen

        layer = screen.AGENT_LAYERS.get(args.get("layer") or "flash")
        if layer is None:
            return False, "layer: flash, pin або idle"
        # Kept as it came, before the check decodes its pictures in place.
        given = _copy(args.get("scene"))
        try:
            scene = screen.check(args.get("scene"), self.screen.width, self.screen.height)
        except screen.SceneError as error:
            return False, str(error)
        args = dict(args)
        args["scene"] = given
        if layer == "idle":
            self._keep_idle(args.get("scene"))
        until = None
        if layer == "flash":
            try:
                hold = int(args.get("seconds") or 10) * 1000
            except (TypeError, ValueError):
                hold = 10_000
            until = now_ms + max(1000, min(hold, SHOW_MAX_MS))
        self.screen.put(layer, scene, until)
        return True, "сцену " + scene.name + " показано на " + (args.get("layer") or "flash")

    def _clear(self, args):
        from heare_panel import screen

        if args.get("id"):
            for layer, name in self.screen.layers().items():
                if name == args["id"] and layer in screen.AGENT_LAYERS.values():
                    self.screen.clear(layer)
                    if layer == "idle":
                        self._keep_idle(None)
                    return True, "прибрано: " + name
            return False, "такої сцени на екрані немає"
        layer = screen.AGENT_LAYERS.get(args.get("layer") or "")
        if layer is None:
            return False, "назви id або layer"
        self.screen.clear(layer)
        if layer == "idle":
            self._keep_idle(None)
        return True, "шар прибрано"

    def _keep_idle(self, scene):
        """The idle screen outlives a restart: kept on the flash, or removed."""
        import os

        try:
            if scene is None:
                os.remove(self.idle_path)
            else:
                import json

                with open(self.idle_path, "w") as handle:
                    handle.write(json.dumps(scene))
        except OSError:
            pass

    def restore(self):
        """The idle screen as it was before the restart, if there was one."""
        from heare_panel import screen

        try:
            import json

            with open(self.idle_path) as handle:
                spec = json.loads(handle.read())
            self.screen.put("idle", screen.check(spec, self.screen.width, self.screen.height))
        except (OSError, ValueError, screen.SceneError):
            return False
        return True

    def _info(self):
        from heare_panel import glyphs, screen, widgets

        try:
            import gc

            gc.collect()
            free = gc.mem_free()
        except (ImportError, AttributeError):
            free = -1
        return {"size": [self.screen.width, self.screen.height],
                "layers": self.screen.layers(),
                "widgets": [k for k in widgets.KINDS if k != "state"],
                "icons": sorted(glyphs.ICON_NAMES), "free": free,
                "max_widgets": screen.MAX_WIDGETS}

    def _picture(self, bitmap, seconds, now_ms):
        import binascii

        try:
            bits = binascii.a2b_base64(bitmap or "")
        except (ValueError, TypeError):
            return False, "картинка не base64"
        if len(bits) != PICTURE_BYTES:
            return False, "картинка має бути " + str(VIEW_W) + "x" + str(VIEW_H)
        try:
            hold = int(seconds or 10) * 1000
        except (TypeError, ValueError):
            hold = 10_000
        self.screen.put("flash", _scenes().picture_scene(bits),
                        now_ms + max(1000, min(hold, SHOW_MAX_MS)))
        return True, "картинку показано"

    def tick(self, now_ms):
        """Keep the light in its mode; called by the display loop."""
        if self.led_mode == "blink":
            lit = (now_ms // BLINK_MS) % 2 == 0
        else:
            lit = self.led_mode == "on"
        led = self._light()
        if led is not None:
            # The board's light is lit by pulling its pin low.
            led.value(0 if lit else 1)

    def levels(self):
        """Every offered pin's level, by its act's name; for the brain's read."""
        found = {}
        for number in FREE_PINS:
            pin = self._outputs.get(number) or self._input(number)
            if pin is None:
                continue
            try:
                found["pin" + str(number)] = 1 if pin.value() else 0
            except (OSError, ValueError):
                continue
        return found

    def _input(self, number):
        pin = self._pins.get(number)
        if pin is None:
            pin = self._pin(number, "in")
            if pin is not None:
                self._pins[number] = pin
        return pin

    def _output(self, number):
        pin = self._outputs.get(number)
        if pin is None:
            pin = self._pin(number, "out")
            if pin is not None:
                self._outputs[number] = pin
                self._pins.pop(number, None)
        return pin

    def _light(self):
        if self._led is None:
            self._led = self._pin(LED_PIN, "out")
        return self._led

    def _pin(self, number, mode):
        if self._make is None:
            return None
        try:
            return self._make(number, mode)
        except Exception:  # noqa: BLE001 - a pin that cannot be had is not offered
            return None


def _copy(value):
    import json

    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return value


def _scenes():
    from heare_panel import screen

    return screen


def board_pin(number, mode):
    """``machine.Pin`` for the board: an input pulled down, or an output."""
    import machine

    if mode == "out":
        return machine.Pin(number, machine.Pin.OUT)
    return machine.Pin(number, machine.Pin.IN, machine.Pin.PULL_DOWN)


def _pin_number(name):
    if not isinstance(name, str) or not name.startswith("pin"):
        return None
    try:
        number = int(name[3:])
    except ValueError:
        return None
    return number if number in FREE_PINS else None


def _after(now_ms, until_ms):
    # The board's ticks wrap; a difference read as signed 30 bits does not.
    return ((now_ms - until_ms) & 0x3FFFFFFF) < 0x20000000
