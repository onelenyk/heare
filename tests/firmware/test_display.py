"""The display adapter, without a display.

A fake I2C records every command and data transfer, and fake driver
modules stand in for the two vendored controllers so the selection order
and the byte layout can be asserted exactly on a laptop. The real drivers
are only imported on the board; they need ``framebuf`` and ``micropython``.
"""

from __future__ import annotations

import json
import sys
import types

import pytest
from heare_panel import display, render
from heare_panel.viewport import Viewport

from tests.firmware.fakes import FakeCanvas, FakeI2C

SDA = 5
SCL = 6
ADDR = 0x3C
SSD1264 = 128 * 64 // 8
SSD7240 = 72 * 40 // 8


class FakeDriver:
    """A driver module that succeeds by default and can fail per geometry."""

    def __init__(self, failures=(), error=None):
        self.failures = set(failures)
        self.error = error or OSError(5, "no display")
        self.calls = []

    def SSD1306_I2C(self, width, height, i2c, addr=ADDR):
        return self._make(width, height, addr)

    def SH1106_I2C(self, width, height, i2c, addr=ADDR):
        return self._make(width, height, addr)

    def _make(self, width, height, addr):
        self.calls.append((width, height, addr))
        if (width, height) in self.failures:
            raise self.error
        return object()


@pytest.fixture
def drivers(monkeypatch):
    ssd = FakeDriver()
    sh = FakeDriver()
    monkeypatch.setattr(display, "ssd1306", ssd)
    monkeypatch.setattr(display, "sh1106", sh)
    monkeypatch.setattr(display, "_none_printed", False)
    return ssd, sh


def write_cache(tmp_path, **values):
    path = tmp_path / "display.json"
    cached = {
        "driver": "ssd1306",
        "width": 128,
        "height": 64,
        "addr": ADDR,
        "sda": SDA,
        "scl": SCL,
    }
    cached.update(values)
    path.write_text(json.dumps(cached), encoding="utf-8")
    return path


def cache_path(tmp_path):
    return tmp_path / "display.json"


def shown(i2c):
    return [
        (addr, data)
        for addr, data in i2c.writes
        if data[:1] == b"\x40"
    ]


def commands(i2c):
    return [(addr, data) for addr, data in i2c.writes if data[:1] == b"\x80"]


def test_selection_tries_big_ssd1306_then_72x40_then_sh1106(drivers, tmp_path):
    ssd, sh = drivers
    ssd.failures = {(128, 64), (72, 40)}
    i2c = FakeI2C()

    chosen = display.select(i2c, SDA, SCL, path=cache_path(tmp_path))

    assert ssd.calls == [(128, 64, ADDR), (72, 40, ADDR)]
    assert sh.calls == [(128, 64, ADDR)]
    assert chosen.available is True
    assert (chosen.driver, chosen.width, chosen.height) == ("sh1106", 128, 64)
    assert (chosen.offset, chosen.addr) == (2, ADDR)
    assert (chosen.view_width, chosen.view_height) == (128, 64)
    assert (chosen.xoff, chosen.yoff) == (0, 0)


def test_the_first_working_candidate_wins(drivers, tmp_path):
    ssd, sh = drivers
    chosen = display.select(FakeI2C(), SDA, SCL, path=cache_path(tmp_path))

    assert ssd.calls == [(128, 64, ADDR)]
    assert sh.calls == []
    assert (chosen.driver, chosen.width, chosen.height, chosen.offset) == (
        "ssd1306",
        128,
        64,
        0,
    )
    assert (chosen.view_width, chosen.view_height) == (72, 40)
    assert (chosen.xoff, chosen.yoff) == (28, 24)


def test_cache_uses_its_geometry_without_trying_others(drivers, tmp_path):
    ssd, sh = drivers
    path = write_cache(tmp_path, driver="ssd1306", width=72, height=40)

    chosen = display.select(FakeI2C(), SDA, SCL, path=path)

    assert ssd.calls == [(72, 40, ADDR)]
    assert sh.calls == []
    assert (chosen.driver, chosen.width, chosen.height, chosen.offset) == (
        "ssd1306",
        72,
        40,
        28,
    )
    assert (chosen.view_width, chosen.view_height) == (72, 40)
    assert (chosen.xoff, chosen.yoff) == (0, 0)


@pytest.mark.parametrize(
    "cached",
    [
        {"driver": "ssd1306", "width": 96, "height": 32},
        {"driver": "unknown", "width": 128, "height": 64},
        {"driver": [], "width": 128, "height": 64},
        {"width": 128, "height": 64},
        {"driver": "ssd1306", "width": "wide", "height": 64},
        {"driver": "ssd1306", "width": 128, "height": 64, "addr": 0x3D},
        {"driver": "ssd1306", "width": 128, "height": 64, "sda": 8},
        {"driver": "ssd1306", "width": 128, "height": 64, "scl": 9},
    ],
)
def test_a_cache_that_does_not_match_the_bus_is_ignored(
    drivers, tmp_path, cached
):
    ssd, _ = drivers
    path = tmp_path / "display.json"
    path.write_text(json.dumps(cached), encoding="utf-8")

    chosen = display.select(FakeI2C(), SDA, SCL, path=path)

    assert ssd.calls == [(128, 64, ADDR)]
    assert chosen.available is True


def test_a_broken_cache_file_is_ignored(drivers, tmp_path):
    ssd, _ = drivers
    path = tmp_path / "display.json"
    path.write_text("{not json", encoding="utf-8")

    chosen = display.select(FakeI2C(), SDA, SCL, path=path)

    assert ssd.calls == [(128, 64, ADDR)]
    assert chosen.available is True


def test_a_none_path_disables_the_cache(drivers):
    chosen = display.select(FakeI2C(), SDA, SCL, path=None)

    assert chosen.available is True
    assert chosen.driver == "ssd1306"


def test_the_working_choice_is_cached(drivers, tmp_path):
    path = cache_path(tmp_path)
    display.select(FakeI2C(), SDA, SCL, path=path)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "driver": "ssd1306",
        "width": 128,
        "height": 64,
        "addr": ADDR,
        "sda": SDA,
        "scl": SCL,
        "view": {
            "width": 72,
            "height": 40,
            "xoff": 28,
            "yoff": 24,
        },
    }


def test_a_legacy_cache_is_migrated_to_the_default_view(drivers, tmp_path):
    ssd, sh = drivers
    path = write_cache(tmp_path)

    chosen = display.select(FakeI2C(), SDA, SCL, path=path)

    assert ssd.calls == [(128, 64, ADDR)]
    assert sh.calls == []
    assert (chosen.view_width, chosen.view_height) == (72, 40)
    assert (chosen.xoff, chosen.yoff) == (28, 24)
    assert json.loads(path.read_text(encoding="utf-8"))["view"] == {
        "width": 72,
        "height": 40,
        "xoff": 28,
        "yoff": 24,
    }


def test_an_explicit_full_view_disables_the_default_window(drivers, tmp_path):
    path = write_cache(
        tmp_path, view={"width": 128, "height": 64, "xoff": 0, "yoff": 0}
    )

    chosen = display.select(FakeI2C(), SDA, SCL, path=path)

    assert (chosen.view_width, chosen.view_height) == (128, 64)
    assert (chosen.xoff, chosen.yoff) == (0, 0)
    canvas = FakeCanvas(128, 64)
    assert chosen.viewport(canvas) is canvas


def test_a_null_view_means_the_whole_controller(drivers, tmp_path):
    path = write_cache(tmp_path, view=None)

    chosen = display.select(FakeI2C(), SDA, SCL, path=path)

    assert (chosen.view_width, chosen.view_height) == (128, 64)
    assert (chosen.xoff, chosen.yoff) == (0, 0)


def test_an_explicit_view_overrides_the_default(drivers, tmp_path):
    path = write_cache(
        tmp_path, view={"width": 72, "height": 40, "xoff": 0, "yoff": 0}
    )

    chosen = display.select(FakeI2C(), SDA, SCL, path=path)

    assert (chosen.view_width, chosen.view_height) == (72, 40)
    assert (chosen.xoff, chosen.yoff) == (0, 0)


@pytest.mark.parametrize(
    "view",
    [
        {"width": 72, "height": 40, "xoff": 100, "yoff": 0},
        {"width": 72, "height": 40, "xoff": 0, "yoff": 30},
        {"width": 0, "height": 40, "xoff": 0, "yoff": 0},
        {"width": 72, "height": 40, "xoff": -1, "yoff": 0},
        {"width": 72, "height": 40, "xoff": 0},
        "full",
    ],
)
def test_an_invalid_view_is_ignored(drivers, tmp_path, view):
    ssd, _ = drivers
    path = write_cache(tmp_path, view=view)

    chosen = display.select(FakeI2C(), SDA, SCL, path=path)

    assert ssd.calls == [(128, 64, ADDR)]
    assert (chosen.view_width, chosen.view_height) == (72, 40)
    assert (chosen.xoff, chosen.yoff) == (28, 24)
    assert json.loads(path.read_text(encoding="utf-8"))["view"] == {
        "width": 72,
        "height": 40,
        "xoff": 28,
        "yoff": 24,
    }


def test_display_viewport_wraps_the_default_window(drivers, tmp_path):
    chosen = display.select(FakeI2C(), SDA, SCL, path=cache_path(tmp_path))
    canvas = FakeCanvas(128, 64)

    viewport = chosen.viewport(canvas)

    assert isinstance(viewport, Viewport)
    assert (viewport.xoff, viewport.yoff) == (28, 24)
    assert (viewport.width, viewport.height) == (72, 40)


def _assert_inside(ops, xoff, yoff, width, height):
    for op in ops:
        kind = op[0]
        if kind == "fill_rect":
            _, x, y, w, h, _ = op
            assert x >= xoff and x + w <= xoff + width
            assert y >= yoff and y + h <= yoff + height
        elif kind == "hline":
            _, x, y, w, _ = op
            assert x >= xoff and x + w <= xoff + width
            assert y >= yoff and y < yoff + height
        elif kind == "vline":
            _, x, y, h, _ = op
            assert x >= xoff and x < xoff + width
            assert y >= yoff and y + h <= yoff + height
        elif kind == "pixel":
            _, x, y, _ = op
            assert x >= xoff and x < xoff + width
            assert y >= yoff and y < yoff + height
        elif kind == "text":
            _, string, x, y, _ = op
            assert x >= xoff and x + 8 * len(string) <= xoff + width
            assert y >= yoff and y + 8 <= yoff + height


def test_a_72x40_render_stays_inside_the_128x64_window():
    canvas = FakeCanvas(128, 64)
    viewport = Viewport(canvas, 28, 24, 72, 40)

    render.render(render.IDLE, viewport, 72, 40)

    assert canvas.ops
    _assert_inside(canvas.ops, 28, 24, 72, 40)
    assert any(op[0] == "text" for op in canvas.ops)
    assert any(op[0] == "pixel" for op in canvas.ops)


def test_flush_ssd1306_sets_the_whole_address_window(drivers, tmp_path):
    i2c = FakeI2C()
    chosen = display.select(i2c, SDA, SCL, path=cache_path(tmp_path))
    frame = bytearray(SSD1264)
    frame[0] = 0x01

    assert chosen.flush(frame) is True

    assert (ADDR, b"\x80\x21") in commands(i2c)
    assert (ADDR, b"\x80\x00") in commands(i2c)
    assert (ADDR, b"\x80\x7F") in commands(i2c)
    assert (ADDR, b"\x80\x22") in commands(i2c)
    assert (ADDR, b"\x80\x07") in commands(i2c)
    assert shown(i2c) == [(ADDR, b"\x40" + bytes(frame))]
    assert len(shown(i2c)[0][1]) == SSD1264 + 1


def test_flush_72x40_starts_at_column_28(drivers, tmp_path):
    path = write_cache(tmp_path, width=72, height=40)
    i2c = FakeI2C()
    chosen = display.select(i2c, SDA, SCL, path=path)
    frame = bytearray(SSD7240)
    frame[0] = 0x01

    assert chosen.flush(frame) is True

    assert (ADDR, b"\x80\x21") in commands(i2c)
    assert (ADDR, b"\x80\x1C") in commands(i2c)
    assert (ADDR, b"\x80\x63") in commands(i2c)
    assert shown(i2c) == [(ADDR, b"\x40" + bytes(frame))]
    assert shown(i2c)[0][1][1] == 0x01


def test_flush_sh1106_writes_each_page_at_column_2(drivers, tmp_path):
    path = write_cache(tmp_path, driver="sh1106")
    i2c = FakeI2C()
    chosen = display.select(i2c, SDA, SCL, path=path)
    frame = bytearray(SSD1264)
    frame[0] = 0x01
    frame[128] = 0x02

    assert chosen.flush(frame) is True

    cmd = [data for _, data in commands(i2c)]
    assert cmd[0:3] == [b"\x80\xB0", b"\x80\x02", b"\x80\x10"]
    assert cmd[3:6] == [b"\x80\xB1", b"\x80\x02", b"\x80\x10"]
    pages = shown(i2c)
    assert len(pages) == 8
    assert pages[0] == (ADDR, b"\x40" + bytes(frame[:128]))
    assert pages[1] == (ADDR, b"\x40" + bytes(frame[128:256]))


def test_flush_rejects_a_frame_of_the_wrong_length(drivers, tmp_path):
    chosen = display.select(FakeI2C(), SDA, SCL, path=cache_path(tmp_path))

    with pytest.raises(ValueError):
        chosen.flush(b"\x00")


def test_flush_on_a_missing_display_is_false_and_silent(drivers, tmp_path):
    ssd, sh = drivers
    ssd.failures = {(128, 64), (72, 40)}
    sh.failures = {(128, 64)}
    i2c = FakeI2C()

    chosen = display.select(i2c, SDA, SCL, path=cache_path(tmp_path))

    assert chosen.flush(b"\x00" * SSD1264) is False
    assert i2c.writes == []


def test_no_display_returns_unavailable_and_says_so_once(
    drivers, tmp_path, capsys
):
    ssd, sh = drivers
    ssd.failures = {(128, 64), (72, 40)}
    sh.failures = {(128, 64)}

    first = display.select(FakeI2C(), SDA, SCL, path=cache_path(tmp_path))
    first_output = capsys.readouterr().out
    second = display.select(FakeI2C(), SDA, SCL, path=cache_path(tmp_path))
    second_output = capsys.readouterr().out

    assert first.available is False
    assert (first.driver, first.width, first.height, first.addr) == (
        None,
        None,
        None,
        None,
    )
    assert (first.view_width, first.view_height) == (None, None)
    assert (first.xoff, first.yoff) == (None, None)
    assert first.viewport(FakeCanvas(128, 64)) is None
    assert first_output == "display=none\n"
    assert second_output == ""
    assert second.available is False


def test_no_display_does_not_leave_a_cache(drivers, tmp_path):
    ssd, sh = drivers
    ssd.failures = {(128, 64), (72, 40)}
    sh.failures = {(128, 64)}
    path = cache_path(tmp_path)

    display.select(FakeI2C(), SDA, SCL, path=path)

    assert not path.exists()


def test_a_driver_that_raises_something_else_still_returns_a_display(
    drivers, tmp_path
):
    ssd, sh = drivers
    ssd.error = RuntimeError("driver bug")
    ssd.failures = {(128, 64), (72, 40)}
    sh.failures = {(128, 64)}

    chosen = display.select(FakeI2C(), SDA, SCL, path=cache_path(tmp_path))

    assert chosen.available is False


def test_self_test_fills_then_clears(drivers, tmp_path, monkeypatch):
    monkeypatch.setattr(display.time, "sleep", lambda seconds: None)
    i2c = FakeI2C()
    chosen = display.select(i2c, SDA, SCL, path=cache_path(tmp_path))
    i2c.writes.clear()

    assert chosen.self_test() is True

    data = [payload for _, payload in shown(i2c)]
    assert data[0] == b"\x40" + b"\xFF" * SSD1264
    assert data[-1] == b"\x40" + b"\x00" * SSD1264


def test_self_test_on_a_missing_display_is_false(drivers, tmp_path):
    ssd, sh = drivers
    ssd.failures = {(128, 64), (72, 40)}
    sh.failures = {(128, 64)}

    chosen = display.select(FakeI2C(), SDA, SCL, path=cache_path(tmp_path))

    assert chosen.self_test() is False


def test_open_builds_the_probe_bus_and_selects(drivers, tmp_path, monkeypatch):
    created = []

    class Pin:
        def __init__(self, number):
            self.number = number

    class I2C(FakeI2C):
        def __init__(self, bus, sda=None, scl=None, freq=0):
            super().__init__()
            self.bus = bus
            self.sda = sda
            self.scl = scl
            self.freq = freq
            created.append(self)

    machine = types.SimpleNamespace(I2C=I2C, Pin=Pin)
    monkeypatch.setitem(sys.modules, "machine", machine)

    chosen = display.open(SDA, SCL, path=cache_path(tmp_path))

    assert chosen.available is True
    assert (chosen.view_width, chosen.view_height) == (72, 40)
    assert (chosen.xoff, chosen.yoff) == (28, 24)
    assert len(created) == 1
    assert created[0].bus == 0
    assert (created[0].sda.number, created[0].scl.number) == (SDA, SCL)
    assert created[0].freq == display.FREQ


def test_open_without_a_bus_is_unavailable(drivers, tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError(19, "ENODEV")

    machine = types.SimpleNamespace(I2C=fail, Pin=lambda number: number)
    monkeypatch.setitem(sys.modules, "machine", machine)

    chosen = display.open(SDA, SCL, path=cache_path(tmp_path))

    assert chosen.available is False
