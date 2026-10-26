"""The screen engine: layers, scenes and layouts — and every screen the panel
drew before the engine, drawn the same through it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from heare_panel import layout, screen

from tests.firmware.screens.capture import screens as drawn_now

GOLDEN = json.loads((Path(__file__).parent / "screens" / "golden.json").read_text("utf-8"))


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_every_screen_is_drawn_as_it_was_before_the_engine(name):
    assert drawn_now()[name] == GOLDEN[name]


def test_the_highest_layer_with_a_scene_is_seen_and_time_takes_it_away():
    seen = screen.Screen()
    assert seen.seen(0)[0] == "face"
    seen.put("pinned", screen.text_scene(["timer"]))
    seen.put("flash", screen.text_scene(["hi"]), until_ms=1000)
    assert seen.seen(500)[0] == "flash"
    assert seen.seen(1001)[0] == "pinned"
    seen.clear("pinned")
    assert seen.seen(1002)[0] == "face"


def test_allow_is_above_everything_and_goes_with_the_permission():
    seen = screen.Screen()
    seen.put("flash", screen.text_scene(["hi"]))
    seen.word("ALLOW")
    assert seen.seen(0)[0] == "system"
    seen.word("IDLE")
    assert seen.seen(0)[0] == "flash"
    assert seen.face.fields["word"] == "IDLE"


def test_the_key_changes_only_when_what_is_seen_changes():
    seen = screen.Screen()
    seen.word("WIFI")
    first = seen.key(0)
    assert seen.key(9000) == first
    seen.put("flash", screen.text_scene(["a"]))
    second = seen.key(0)
    assert second != first
    seen.put("flash", screen.text_scene(["a"]))
    assert seen.key(0) != second  # a new scene of the same words is drawn anew


def test_an_update_that_changes_a_field_changes_the_key():
    scene = screen.Scene("weather", {"widget": "text", "bind": "lines"}, {"lines": ["21"]})
    before = scene.key(72, 40, 0)
    assert scene.update({"lines": ["21"]}) is False and scene.key(72, 40, 0) == before
    assert scene.update({"lines": ["22"]}) is True and scene.key(72, 40, 0) != before


def test_rows_and_columns_fill_the_window_with_a_pixel_between():
    spec = {"rows": [{"h": 1, "cols": [{"widget": "a"}, {"w": 2, "widget": "b"}]},
                     {"h": 2, "widget": "c"}]}
    placed = [(box, w["widget"]) for box, w in layout.boxes(spec, 0, 0, 72, 40)]
    assert placed == [((0, 0, 23, 13), "a"), ((24, 0, 48, 13), "b"), ((0, 14, 72, 26), "c")]


def test_a_layout_that_cannot_be_laid_out_says_why():
    with pytest.raises(layout.LayoutError, match="вага"):
        layout.boxes({"rows": [{"h": 0, "widget": "a"}]}, 0, 0, 72, 40)
    with pytest.raises(layout.LayoutError, match="не вміщається"):
        layout.boxes({"cols": [{"widget": "a"}] * 40}, 0, 0, 72, 40)
    with pytest.raises(layout.LayoutError, match="вкладених"):
        deep = {"rows": [{"rows": [{"rows": [{"rows": [{"widget": "a"}]}]}]}]}
        layout.boxes(deep, 0, 0, 72, 40)
