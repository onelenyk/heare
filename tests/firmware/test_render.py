"""The renderer is a contract with a 1-bit screen, tested without one.

The table the tests are built around:

    size       grid     word row   icon row   detail row
    ---------  -------  ---------  ---------  ----------
    72x40      9x5      0          3          none
    128x64     16x8     0          2          5

Every state must fit, carry an icon, and draw only the word (plus, on the
large panel, the detail word). ALLOW is the one state allowed to blink: the
second half of every second is blank, and every other state must render the
same bytes whatever the clock says.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from heare_panel import render, state

from tests.firmware.fakes import FakeCanvas
from tests.firmware.frame import frame

SIZES = ((72, 40), (128, 64))
LAYOUT = {
    (72, 40): {"rows": (0, 3), "chars": 9, "detail": None},
    (128, 64): {"rows": (0, 2), "chars": 16, "detail": 5},
}
ALLOWED = set(render.WORDS.values()) | set(render.DETAILS.values())


def drawn(name: str, width: int, height: int, now_ms: int = 0) -> list:
    canvas = FakeCanvas(width, height)
    render.render(name, canvas, width, height, now_ms)
    return canvas.ops


def texts(ops: list) -> list[tuple[str, int, int]]:
    return [(op[1], op[2], op[3]) for op in ops if op[0] == "text"]


def test_states_do_not_drift_from_the_reducer():
    assert render.STATES == state.STATES
    assert tuple(render.WORDS) == state.STATES
    assert set(render.DETAILS) == set(state.STATES)
    assert set(render._ICONS) == set(state.STATES)


def test_renderer_imports_nothing():
    tree = ast.parse(Path(render.__file__).read_text(encoding="utf-8"))
    imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert imports == []


def test_firmware_render_has_no_font_table():
    text = Path(render.__file__).read_text(encoding="utf-8")
    assert "_FONT" not in text
    assert "def frame(" not in text
    tree = ast.parse(text)
    render_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "render"
    )
    calls_text = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "text"
        for node in ast.walk(render_function)
    )
    assert calls_text


@pytest.mark.parametrize("name", render.STATES)
@pytest.mark.parametrize("width,height", SIZES)
def test_every_state_renders_a_non_empty_frame(name: str, width: int, height: int):
    ops = drawn(name, width, height)
    assert ops
    assert texts(ops)[0][0] == name
    assert any(op[0] == "pixel" for op in ops)

    buffer = frame(name, width, height)
    assert isinstance(buffer, bytearray)
    assert len(buffer) == width * height // 8
    assert any(buffer), f"{name} at {width}x{height} is blank"


@pytest.mark.parametrize("name", render.STATES)
@pytest.mark.parametrize("width,height", SIZES)
def test_the_word_fits(name: str, width: int, height: int):
    layout = LAYOUT[(width, height)]
    word, x, y = texts(drawn(name, width, height))[0]
    assert len(word) <= layout["chars"]
    assert 0 <= x
    assert x + 8 * len(word) <= width
    assert 0 <= y
    assert y + 8 <= height


@pytest.mark.parametrize("name", render.STATES)
def test_word_goes_through_canvas_text_at_the_centred_position(name: str):
    word, x, y = texts(drawn(name, 72, 40))[0]
    assert word == name
    assert y == 0
    assert x == (72 - 8 * len(word)) // 2


@pytest.mark.parametrize("name", render.STATES)
def test_small_panel_has_word_and_icon_only(name: str):
    ops = drawn(name, 72, 40)
    assert len(texts(ops)) == 1
    icon_y = [op[2] for op in ops if op[0] == "pixel"]
    assert icon_y
    assert all(24 <= y < 32 for y in icon_y)


@pytest.mark.parametrize("name", render.STATES)
def test_large_panel_adds_the_detail_line(name: str):
    pos, x, y = texts(drawn(name, 128, 64))[1]
    assert pos == render.DETAILS[name]
    assert y == 5 * 8
    assert x + 8 * len(pos) <= 128


@pytest.mark.parametrize("name", render.STATES)
def test_only_the_whitelisted_words_are_drawn(name: str):
    for string, _x, _y in texts(drawn(name, 128, 64)):
        assert string in ALLOWED
        assert all(char == " " or "A" <= char <= "Z" for char in string)


def test_no_text_field_or_cyrillic_reaches_the_screen():
    for name in render.STATES:
        for width, height in SIZES:
            for string, _x, _y in texts(drawn(name, width, height)):
                assert not any("\u0400" <= char <= "\u04ff" for char in string)
                assert not any("\u4e00" <= char <= "\u9fff" for char in string)


def test_allow_blinks_but_the_others_do_not():
    visible = drawn(render.ALLOW, 128, 64, 0)
    assert visible == drawn(render.ALLOW, 128, 64, 100)
    assert visible != drawn(render.ALLOW, 128, 64, 500)
    assert drawn(render.ALLOW, 128, 64, 500) == [("fill", 0)]
    assert visible == drawn(render.ALLOW, 128, 64, 1000)

    for name in render.STATES:
        if name == render.ALLOW:
            continue
        baseline = drawn(name, 72, 40, 0)
        assert baseline == drawn(name, 72, 40, 500)
        assert baseline == drawn(name, 72, 40, 12_345)


@pytest.mark.parametrize("name", render.STATES)
@pytest.mark.parametrize("width,height", SIZES)
def test_frame_ops_is_the_same_trace(name: str, width: int, height: int):
    assert render.frame_ops(name, width, height) == drawn(name, width, height)


def test_frame_is_page_packed_and_deterministic():
    buffer = frame(render.IDLE, 72, 40)
    assert frame(render.IDLE, 72, 40) == buffer

    (x, y) = 33, 26
    index = y * (72 // 8) + (x >> 3)
    bit = 1 << (x & 7)
    assert buffer[index] & bit

    blank = frame(render.ALLOW, 128, 64, 500)
    assert set(blank) == {0}


def test_unknown_state_or_size_is_refused():
    with pytest.raises(ValueError):
        render.render("WHAT", FakeCanvas(72, 40), 72, 40, 0)
    with pytest.raises(ValueError):
        render.render(render.IDLE, FakeCanvas(96, 48), 96, 48, 0)
    with pytest.raises(ValueError):
        frame(render.IDLE, 71, 40)


def test_golden_idle_on_the_small_panel():
    assert render.frame_ops(render.IDLE, 72, 40) == [
        ("fill", 0),
        ("text", "IDLE", 20, 0, 1),
        ("pixel", 33, 26, 1),
        ("pixel", 34, 26, 1),
        ("pixel", 37, 26, 1),
        ("pixel", 38, 26, 1),
        ("pixel", 32, 27, 1),
        ("pixel", 35, 27, 1),
        ("pixel", 36, 27, 1),
        ("pixel", 39, 27, 1),
        ("pixel", 32, 28, 1),
        ("pixel", 35, 28, 1),
        ("pixel", 36, 28, 1),
        ("pixel", 39, 28, 1),
        ("pixel", 33, 29, 1),
        ("pixel", 34, 29, 1),
        ("pixel", 37, 29, 1),
        ("pixel", 38, 29, 1),
    ]


def test_golden_allow_on_the_large_panel():
    assert render.frame_ops(render.ALLOW, 128, 64, 0) == [
        ("fill", 0),
        ("text", "ALLOW", 44, 0, 1),
        ("pixel", 67, 17, 1),
        ("pixel", 66, 18, 1),
        ("pixel", 65, 19, 1),
        ("pixel", 60, 20, 1),
        ("pixel", 64, 20, 1),
        ("pixel", 61, 21, 1),
        ("pixel", 63, 21, 1),
        ("pixel", 62, 22, 1),
        ("text", "APPROVE", 36, 40, 1),
    ]


def test_golden_error_on_the_small_panel():
    assert render.frame_ops(render.ERROR, 72, 40) == [
        ("fill", 0),
        ("text", "ERROR", 16, 0, 1),
        ("pixel", 33, 25, 1),
        ("pixel", 38, 25, 1),
        ("pixel", 34, 26, 1),
        ("pixel", 37, 26, 1),
        ("pixel", 35, 27, 1),
        ("pixel", 36, 27, 1),
        ("pixel", 35, 28, 1),
        ("pixel", 36, 28, 1),
        ("pixel", 34, 29, 1),
        ("pixel", 37, 29, 1),
        ("pixel", 33, 30, 1),
        ("pixel", 38, 30, 1),
    ]
