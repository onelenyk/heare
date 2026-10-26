"""The viewport must offset and clip every primitive.

The board's 128x64 controller shows only a 72x40 window at (28,24). These
tests use a recording canvas to assert that nothing can be drawn outside
that window, including text, which is clipped by whole 8-pixel cells.
"""

from __future__ import annotations

import pytest
from heare_panel.viewport import CELL, Viewport

from tests.firmware.fakes import FakeCanvas

XOFF = 28
YOFF = 24
WIDTH = 72
HEIGHT = 40


def make():
    canvas = FakeCanvas(128, 64)
    return canvas, Viewport(canvas, XOFF, YOFF, WIDTH, HEIGHT)


def test_fill_covers_only_the_window():
    canvas, viewport = make()
    viewport.fill(1)
    assert canvas.ops == [("fill_rect", XOFF, YOFF, WIDTH, HEIGHT, 1)]


def test_fill_rect_is_offset_and_clipped():
    canvas, viewport = make()

    viewport.fill_rect(10, 10, 5, 5, 1)
    viewport.fill_rect(-5, -5, 10, 10, 1)
    viewport.fill_rect(70, 38, 10, 10, 1)
    viewport.fill_rect(72, 0, 10, 10, 1)
    viewport.fill_rect(-10, -10, 5, 5, 1)
    viewport.fill_rect(0, 0, 0, 10, 1)
    viewport.fill_rect(0, 0, 10, -5, 1)

    assert canvas.ops == [
        ("fill_rect", 38, 34, 5, 5, 1),
        ("fill_rect", 28, 24, 5, 5, 1),
        ("fill_rect", 98, 62, 2, 2, 1),
    ]


def test_rect_draws_four_clipped_lines():
    canvas, viewport = make()

    viewport.rect(0, 0, 4, 4, 1)
    viewport.rect(-2, -2, 4, 4, 1)
    viewport.rect(70, 38, 4, 4, 1)
    viewport.rect(0, 0, 0, 4, 1)

    assert canvas.ops == [
        ("hline", 28, 24, 4, 1),
        ("hline", 28, 27, 4, 1),
        ("vline", 28, 24, 4, 1),
        ("vline", 31, 24, 4, 1),
        ("hline", 28, 25, 2, 1),
        ("vline", 29, 24, 2, 1),
        ("hline", 98, 62, 2, 1),
        ("vline", 98, 62, 2, 1),
    ]


def test_text_is_offset_when_it_fits():
    canvas, viewport = make()

    viewport.text("IDLE", 20, 0, 1)

    assert canvas.ops == [("text", "IDLE", 48, 24, 1)]
    assert CELL == 8


def test_text_is_clipped_at_the_left_edge():
    canvas, viewport = make()

    viewport.text("AB", -4, 0, 1)
    viewport.text("A", -10, 0, 1)

    assert canvas.ops == [("text", "B", 32, 24, 1)]


def test_text_is_clipped_at_the_right_edge():
    canvas, viewport = make()

    viewport.text("ABCDEFGHIJ", 60, 0, 1)
    viewport.text("ABCDEFGHIJ", 68, 0, 1)

    assert canvas.ops == [("text", "A", 88, 24, 1)]


def test_text_outside_vertically_is_not_drawn():
    canvas, viewport = make()

    viewport.text("A", 0, -1, 1)
    viewport.text("A", 0, 33, 1)
    viewport.text("A", 0, 32, 1)
    viewport.text("", 0, 0, 1)

    assert canvas.ops == [("text", "A", 28, 56, 1)]


def test_pixel_is_offset_and_clipped():
    canvas, viewport = make()

    viewport.pixel(0, 0, 1)
    viewport.pixel(71, 39, 1)
    viewport.pixel(-1, 0, 1)
    viewport.pixel(72, 0, 1)
    viewport.pixel(0, -1, 1)
    viewport.pixel(0, 40, 1)

    assert canvas.ops == [
        ("pixel", 28, 24, 1),
        ("pixel", 99, 63, 1),
    ]


def test_hline_is_offset_and_clipped():
    canvas, viewport = make()

    viewport.hline(0, 0, 10, 1)
    viewport.hline(-5, 0, 10, 1)
    viewport.hline(70, 0, 10, 1)
    viewport.hline(0, -1, 10, 1)
    viewport.hline(0, 40, 10, 1)
    viewport.hline(72, 0, 10, 1)

    assert canvas.ops == [
        ("hline", 28, 24, 10, 1),
        ("hline", 28, 24, 5, 1),
        ("hline", 98, 24, 2, 1),
    ]


def test_vline_is_offset_and_clipped():
    canvas, viewport = make()

    viewport.vline(0, 0, 10, 1)
    viewport.vline(0, -5, 10, 1)
    viewport.vline(0, 38, 10, 1)
    viewport.vline(-1, 0, 10, 1)
    viewport.vline(72, 0, 10, 1)
    viewport.vline(0, 40, 10, 1)

    assert canvas.ops == [
        ("vline", 28, 24, 10, 1),
        ("vline", 28, 24, 5, 1),
        ("vline", 28, 62, 2, 1),
    ]


@pytest.mark.parametrize(
    "name",
    ["fill", "fill_rect", "rect", "text", "pixel", "hline", "vline"],
)
def test_the_seven_canvas_methods_exist(name):
    _, viewport = make()
    assert callable(getattr(viewport, name))


def test_a_full_view_adds_no_offset():
    canvas = FakeCanvas(128, 64)
    viewport = Viewport(canvas, 0, 0, 128, 64)

    viewport.fill(0)
    viewport.pixel(0, 0, 1)
    viewport.text("A", 0, 0, 1)

    assert canvas.ops == [
        ("fill_rect", 0, 0, 128, 64, 0),
        ("pixel", 0, 0, 1),
        ("text", "A", 0, 0, 1),
    ]
