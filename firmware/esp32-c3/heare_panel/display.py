"""The panel's only hardware adapter: an SSD1306 or SH1106 on I2C.

``render`` is pure and draws into anything with a canvas interface; this
module is what turns those pixels into bytes on the board. It knows the
I2C transport and the two controller protocols, and nothing else.

The controller on this board is 128x64, but only the 72x40 window at
(28,24) is visible. ``Display`` records both geometries; drawing goes
through ``Viewport`` so the 72x40 renderer lands in that window instead of
the top-left corner of the controller.

Selection is cached in ``/display.json`` because probing writes to the
bus and is worth skipping on every boot. The cache is a cache, not proof:
a panel that answers an init sequence is not necessarily the geometry the
init claims, which is why the device tool photographs a full frame.

A missing or unhappy display is never fatal. ``select`` returns a Display
whose ``available`` is false and prints ``display=none`` once; the network
side of the panel does not import this module's bus at all.
"""

import json
import time

from heare_panel.viewport import Viewport

# Keep the built-in before ``open`` below shadows it for the module.
_open = open

CACHE_PATH = "/display.json"
DEFAULT_SDA = 5
DEFAULT_SCL = 6
DEFAULT_ADDR = 0x3C
FREQ = 400000
SSD1306 = "ssd1306"
SH1106 = "sh1106"

# The probe found 0x3C on these pins; the cache repeats them so a stale
# file from another board or a re-wired display is ignored instead of used.
_OFFSETS = {
    (SSD1306, 128, 64): 0,
    (SSD1306, 72, 40): 28,
    (SH1106, 128, 64): 2,
}

_CANDIDATES = (
    (SSD1306, 128, 64),
    (SSD1306, 72, 40),
    (SH1106, 128, 64),
)

# The visible window inside each controller. This board's 128x64 controller
# exposes only the 72x40 panel at (28,24); the fallback geometries use the
# whole panel. A cache ``view`` overrides these defaults.
_PROFILES = {
    (SSD1306, 128, 64): (72, 40, 28, 24),
    (SSD1306, 72, 40): (72, 40, 0, 0),
    (SH1106, 128, 64): (128, 64, 0, 0),
}

ssd1306 = None
sh1106 = None
_none_printed = False


class Display:
    """A selected panel, or the recorded absence of one."""

    __slots__ = (
        "_i2c",
        "_panel",
        "addr",
        "available",
        "driver",
        "height",
        "offset",
        "scl",
        "sda",
        "view_height",
        "view_width",
        "width",
        "xoff",
        "yoff",
    )

    def __init__(
        self,
        available,
        driver,
        width,
        height,
        view_width,
        view_height,
        xoff,
        yoff,
        addr,
        offset,
        sda,
        scl,
        i2c,
        panel,
    ):
        self.available = available
        self.driver = driver
        self.width = width
        self.height = height
        self.view_width = view_width
        self.view_height = view_height
        self.xoff = xoff
        self.yoff = yoff
        self.addr = addr
        self.offset = offset
        self.sda = sda
        self.scl = scl
        self._i2c = i2c
        self._panel = panel

    def viewport(self, canvas):
        """Wrap a canvas in the visible window, or return it when full."""
        if not self.available:
            return None
        if (
            self.xoff == 0
            and self.yoff == 0
            and self.view_width == self.width
            and self.view_height == self.height
        ):
            return canvas
        return Viewport(
            canvas, self.xoff, self.yoff, self.view_width, self.view_height
        )

    def flush(self, frame):
        """Write one page-packed frame; false when the panel went away."""
        if not self.available:
            return False
        size = self.width * self.height // 8
        if len(frame) != size:
            raise ValueError(f"frame must be {size} bytes, not {len(frame)}")
        try:
            if self.driver == SSD1306:
                self._flush_ssd1306(frame)
            elif self.driver == SH1106:
                self._flush_sh1106(frame)
            else:
                return False
        except OSError:
            self.available = False
            _announce_none()
            return False
        return True

    def _flush_ssd1306(self, frame):
        pages = self.height // 8
        self._command(0x21)
        self._command(self.offset)
        self._command(self.offset + self.width - 1)
        self._command(0x22)
        self._command(0)
        self._command(pages - 1)
        self._i2c.writeto(self.addr, b"\x40" + bytes(frame))

    def _flush_sh1106(self, frame):
        data = bytes(frame)
        for page in range(self.height // 8):
            self._command(0xB0 | page)
            self._command(0x00 | (self.offset & 0x0F))
            self._command(0x10 | (self.offset >> 4))
            start = page * self.width
            self._i2c.writeto(
                self.addr, b"\x40" + data[start : start + self.width]
            )

    def _command(self, value):
        self._i2c.writeto(self.addr, bytes((0x80, value)))

    def self_test(self):
        """Fill the panel, hold it for a second, then clear it."""
        if not self.available:
            return False
        size = self.width * self.height // 8
        if not self.flush(b"\xff" * size):
            return False
        time.sleep(1)
        return self.flush(b"\x00" * size)


def select(i2c, sda=DEFAULT_SDA, scl=DEFAULT_SCL, addr=DEFAULT_ADDR, path=CACHE_PATH):
    """Pick a working panel from the cache or the probe's candidate order."""
    try:
        cached = _read_cache(path)
        if cached is not None:
            choice = _cached_choice(cached, sda, scl, addr)
            if choice is not None:
                panel = _build(choice[0], choice[1], choice[2], i2c, addr)
                if panel is not None:
                    chosen = _display(choice, sda, scl, addr, i2c, panel)
                    if "view" not in cached:
                        _write_cache(chosen, path)
                    return chosen
        for driver, width, height in _CANDIDATES:
            panel = _build(driver, width, height, i2c, addr)
            if panel is not None:
                offset = _OFFSETS[(driver, width, height)]
                view_width, view_height, xoff, yoff = _PROFILES[
                    (driver, width, height)
                ]
                chosen = _display(
                    (
                        driver,
                        width,
                        height,
                        offset,
                        view_width,
                        view_height,
                        xoff,
                        yoff,
                    ),
                    sda,
                    scl,
                    addr,
                    i2c,
                    panel,
                )
                _write_cache(chosen, path)
                return chosen
    except Exception:  # noqa: BLE001 - a broken bus must not stop the boot
        return _unavailable(sda, scl)
    return _unavailable(sda, scl)


def open(sda=DEFAULT_SDA, scl=DEFAULT_SCL, addr=DEFAULT_ADDR, path=CACHE_PATH):
    """Create the bus and select a panel; never raise toward the boot path."""
    try:
        import machine

        i2c = machine.I2C(
            0,
            sda=machine.Pin(sda),
            scl=machine.Pin(scl),
            freq=FREQ,
        )
    except Exception:  # noqa: BLE001 - the display is never the boot gate
        return _unavailable(sda, scl)
    return select(i2c, sda, scl, addr=addr, path=path)


def _display(choice, sda, scl, addr, i2c, panel):
    driver, width, height, offset, view_width, view_height, xoff, yoff = choice
    return Display(
        True,
        driver,
        width,
        height,
        view_width,
        view_height,
        xoff,
        yoff,
        addr,
        offset,
        sda,
        scl,
        i2c,
        panel,
    )


def _unavailable(sda, scl):
    _announce_none()
    return Display(
        False,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        sda,
        scl,
        None,
        None,
    )


def _announce_none():
    global _none_printed
    if not _none_printed:
        _none_printed = True
        print("display=none")


def _module(name):
    global ssd1306, sh1106
    if name == SSD1306:
        if ssd1306 is None:
            try:
                import ssd1306 as module
            except ImportError:
                return None
            ssd1306 = module
        return ssd1306
    if sh1106 is None:
        try:
            import sh1106 as module
        except ImportError:
            return None
        sh1106 = module
    return sh1106


def _build(driver, width, height, i2c, addr):
    module = _module(driver)
    if module is None:
        return None
    try:
        if driver == SSD1306:
            return module.SSD1306_I2C(width, height, i2c, addr=addr)
        return module.SH1106_I2C(width, height, i2c, addr=addr)
    except Exception:  # noqa: BLE001 - a bad driver is a missing display
        return None


def _read_cache(path):
    if path is None:
        return None
    try:
        with _open(path) as handle:
            value = json.loads(handle.read())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _cached_choice(value, sda, scl, addr):
    try:
        driver = value["driver"]
        width = int(value["width"])
        height = int(value["height"])
        cached_addr = int(value.get("addr", addr))
        cached_sda = int(value.get("sda", sda))
        cached_scl = int(value.get("scl", scl))
    except (KeyError, TypeError, ValueError):
        return None
    if not isinstance(driver, str):
        return None
    if cached_addr != addr or cached_sda != sda or cached_scl != scl:
        return None
    key = (driver, width, height)
    if key not in _OFFSETS:
        return None
    view = _cached_view(value, width, height)
    if view is None:
        return None
    return key + (_OFFSETS[key],) + view


def _cached_view(value, width, height):
    if "view" not in value:
        return _PROFILES[(value["driver"], width, height)]
    raw = value["view"]
    if raw is None:
        return (width, height, 0, 0)
    if not isinstance(raw, dict):
        return None
    try:
        view_width = int(raw["width"])
        view_height = int(raw["height"])
        xoff = int(raw["xoff"])
        yoff = int(raw["yoff"])
    except (KeyError, TypeError, ValueError):
        return None
    if view_width <= 0 or view_height <= 0 or xoff < 0 or yoff < 0:
        return None
    if xoff + view_width > width or yoff + view_height > height:
        return None
    return (view_width, view_height, xoff, yoff)


def _write_cache(display, path):
    if path is None:
        return
    value = {
        "driver": display.driver,
        "width": display.width,
        "height": display.height,
        "addr": display.addr,
        "sda": display.sda,
        "scl": display.scl,
        "view": {
            "width": display.view_width,
            "height": display.view_height,
            "xoff": display.xoff,
            "yoff": display.yoff,
        },
    }
    try:
        with _open(path, "w") as handle:
            handle.write(json.dumps(value))
    except OSError:
        pass
