"""An SVG the model wrote, as the pixels a part's screen takes."""

from __future__ import annotations

import base64

import pytest

from heare import pixels
from heare.native_hands import _rendered

SPEC = {"to": "mono", "width": 72, "height": 40, "as": "bitmap"}
DOT = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 40">'
       '<circle cx="36" cy="20" r="12" fill="white"/></svg>')


def _lit(encoded: str, x: int, y: int) -> bool:
    bits = base64.b64decode(encoded)
    return bool(bits[y * 9 + x // 8] & (0x80 >> (x % 8)))


def test_a_circle_is_lit_in_the_middle_and_dark_in_the_corners():
    drawn = pixels.render(DOT, SPEC)
    assert len(base64.b64decode(drawn)) == 360
    assert _lit(drawn, 36, 20) and _lit(drawn, 30, 20)
    assert not _lit(drawn, 0, 0) and not _lit(drawn, 71, 39) and not _lit(drawn, 10, 20)


def test_nothing_the_svg_names_outside_itself_is_kept():
    dirty = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
             '<script>alert(1)</script><image href="file:///etc/passwd"/>'
             '<foreignObject><div/></foreignObject>'
             '<a xlink:href="https://evil.example"><rect onclick="x()" width="9" height="9"/></a>'
             '<use href="#ok"/></svg>')
    kept = pixels.clean(dirty)
    for gone in ("script", "<image", "foreignObject", "evil.example", "onclick", "<use"):
        assert gone not in kept


def test_an_empty_or_all_white_picture_is_said_so_to_the_model():
    with pytest.raises(pixels.Unfit, match="порожня"):
        pixels.render('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 40"/>', SPEC)
    with pytest.raises(pixels.Unfit, match="білою"):
        pixels.render('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 40">'
                      '<rect width="72" height="40" fill="white"/></svg>', SPEC)
    with pytest.raises(pixels.Unfit):
        pixels.render("not svg at all", SPEC)


def test_bits_are_rows_eight_pixels_a_byte_leftmost_highest():
    assert pixels.mono([[True] + [False] * 7 + [False] * 7 + [True]]) == bytes([0x80, 0x01])


def test_the_hands_render_what_the_skill_asks_and_pass_the_rest():
    skill = {"render": {"svg": SPEC}}
    args = _rendered(skill, {"svg": DOT, "seconds": 5})
    assert set(args) == {"bitmap", "seconds"} and args["seconds"] == 5
    assert _rendered({}, {"svg": DOT}) == {"svg": DOT}
