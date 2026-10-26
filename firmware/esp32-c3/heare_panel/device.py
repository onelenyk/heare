"""What the agent may do with the board: words on the screen, the light, pins.

The panel names its acts in the knock; the brain only forwards ``control``
events for acts named here, and decides which of them wait for a person's
yes (the pins). This module checks again anyway: a name or a value it does
not know is dropped, never guessed at.

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
FREE_PINS = (0, 1, 2, 3, 4, 7, 10, 20, 21)
LINE_CHARS = 9
MAX_LINES = 4
SHOW_MAX_MS = 60_000
BLINK_MS = 400


def acts():
    """The acts the knock offers the agent."""
    found = [{"name": "show", "kind": "show"}, {"name": "led", "kind": "light"}]
    for number in FREE_PINS:
        found.append({"name": "pin" + str(number), "kind": "switch"})
    return found


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

    def __init__(self, pin_factory=None):
        self._make = pin_factory
        self._pins = {}
        self._outputs = {}
        self._led = None
        self.led_mode = "off"
        self.shown = None  # (lines, until_ms)

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
            self.shown = (shown, now_ms + hold)
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

    def text(self, now_ms):
        """The lines to show now, or None once their time is up."""
        shown = self.shown
        if shown is None:
            return None
        if _after(now_ms, shown[1]):
            self.shown = None
            return None
        return shown[0]

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
