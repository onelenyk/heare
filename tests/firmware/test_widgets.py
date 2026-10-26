"""The widgets, drawn: where they sit and what they light."""

from __future__ import annotations

import pytest
from heare_panel import font, glyphs, screen, widgets

from tests.firmware.raster import Raster


@pytest.fixture(autouse=True)
def _letters(monkeypatch):
    # Size 2 reads the board's letters back; on a laptop each is a box.
    monkeypatch.setattr(font, "ascii_rows", lambda char: bytes([0x7E] * 7 + [0]))
    font._cache.clear()


def _draw(spec, fields=None, now=0):
    raster = Raster()
    screen.Scene("t", spec, fields).draw(raster, 72, 40, now)
    return raster


def test_ukrainian_letters_are_the_panel_s_own_and_lookalikes_are_latin():
    assert font.plain("Привіт, світ!") == "Пpивiт, cвiт!"
    assert font.plain("日本") == "??"
    drawn = Raster()
    font.draw(drawn, "Пр", 0, 0)
    letter = glyphs.letter("П")
    for row in range(8):
        assert [drawn.lit[row][c] for c in range(8)] == [bool(letter[row] & (0x80 >> c)) for c in range(8)]
    assert drawn.lit[0][8]  # «р» is the board's «p», a box here


def test_words_wrap_to_the_box_and_a_long_word_is_cut():
    plain = lambda lines: [font.plain(line) for line in lines]  # noqa: E731
    assert widgets.wrap("чайник уже закипів", 9, 4) == plain(["чайник", "уже", "закипів"])
    assert widgets.wrap("абвгґдеєжзиі", 9, 4) == plain(["абвгґдеєж", "зиі"])
    assert widgets.wrap("а б в г д е", 3, 2) == plain(["а б", "в г"])


def test_text_of_size_two_is_twice_as_tall():
    one = _draw({"widget": "text", "text": "Ш"})
    two = _draw({"widget": "text", "text": "Ш", "size": 2})
    rows = lambda r: [y for y, line in enumerate(r.lit) if any(line)]  # noqa: E731
    assert len(rows(two)) == 2 * len(rows(one))


def test_a_number_is_big_when_it_fits_and_small_when_it_does_not():
    big = _draw({"widget": "number", "bind": "temp", "unit": "°"}, {"temp": 21})
    small = _draw({"widget": "number", "value": "123456789", "unit": ""})
    tall = lambda r: max(y for y, line in enumerate(r.lit) if any(line)) - min(  # noqa: E731
        y for y, line in enumerate(r.lit) if any(line))
    assert tall(big) >= 13 and tall(small) <= 7


def test_an_icon_lights_its_own_pixels_in_the_middle():
    drawn = _draw({"widget": "icon", "name": "heart"})
    heart = glyphs.icon("heart")
    assert drawn.count() == sum(bin(b).count("1") for b in heart)
    assert not any(drawn.lit[y][x] for y in range(40) for x in range(0, 20))


def test_a_bar_is_filled_as_far_as_its_value():
    half = _draw({"widget": "bar", "bind": "p"}, {"p": 50})
    full = _draw({"widget": "bar", "value": 100})
    middle = 20
    assert sum(half.lit[middle]) < sum(full.lit[middle])
    assert full.lit[middle][2] and full.lit[middle][69]


def test_a_layout_puts_each_widget_in_its_own_box():
    drawn = _draw({"cols": [{"widget": "icon", "name": "sun"},
                            {"w": 2, "widget": "number", "value": 21, "unit": "°"}]})
    left = sum(drawn.lit[y][x] for y in range(40) for x in range(23))
    right = sum(drawn.lit[y][x] for y in range(40) for x in range(24, 72))
    assert left > 20 and right > 20


def test_the_clock_and_the_spinner_ask_for_frames_and_still_widgets_do_not():
    box = (0, 0, 72, 40)
    assert widgets.key({"widget": "spinner"}, {}, 0, box) != widgets.key({"widget": "spinner"}, {}, 130, box)
    assert widgets.key({"widget": "number", "value": 1}, {}, 0, box) is None
    assert len(widgets.key({"widget": "clock"}, {}, 0, box)) == 3
    assert _draw({"widget": "spinner"}).count() > 8
    assert _draw({"widget": "clock"}).count() > 20


def test_big_text_that_would_break_a_word_is_drawn_at_size_one():
    rows = lambda r: [y for y, line in enumerate(r.lit) if any(line)]  # noqa: E731
    copying = _draw({"rows": [{"h": 2, "widget": "text", "text": "Копіюю", "size": 2},
                              {"widget": "bar", "value": 40}]})
    first = [y for y in rows(copying) if y < 20]
    assert max(first) - min(first) <= 8  # one line of the small letters, «Копіюю» whole
    start = _draw({"cols": [{"widget": "icon", "name": "sun"},
                            {"w": 2, "widget": "text", "text": "Старт", "size": 2}]})
    assert max(rows(start)) - min(y for y in rows(start) if any(start.lit[y][24:])) <= 16


def test_an_icon_drawn_from_svg_is_sixteen_square_in_any_box():
    import base64

    bits = base64.b64decode(base64.b64encode(bytes([0xFF, 0xFF] * 16)))
    drawn = _draw({"cols": [{"widget": "icon", "bits": bits}, {"w": 2, "widget": "text", "text": ""}]})
    lit = [(x, y) for y in range(40) for x in range(72) if drawn.lit[y][x]]
    xs, ys = {x for x, _ in lit}, {y for _, y in lit}
    assert len(lit) == 256 and max(xs) - min(xs) == 15 and max(ys) - min(ys) == 15
