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
    with pytest.raises(pixels.Unfit, match="empty"):
        pixels.render('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 40"/>', SPEC)
    with pytest.raises(pixels.Unfit, match="all white"):
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


def test_deep_rules_render_an_icon_small_and_a_picture_whole():
    rules = [{"deep": True, "key": "svg", "where": {"widget": "icon"}, "to": "mono",
              "width": 16, "height": 16, "as": "bits"},
             {"deep": True, "key": "svg", "where": {"widget": "picture"}, "to": "mono",
              "width": 72, "height": 40, "as": "bits"}]
    icon_svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
                '<circle cx="8" cy="8" r="6" fill="white"/></svg>')
    scene = {"id": "s", "layout": {"cols": [{"widget": "icon", "svg": icon_svg},
                                            {"widget": "picture", "svg": DOT},
                                            {"widget": "text", "text": "svg"}]}}
    args = {"scene": scene, "layer": "pin"}

    done = _rendered({"render": {"scene": rules}}, args)

    icon, picture, text = done["scene"]["layout"]["cols"]
    assert len(base64.b64decode(icon["bits"])) == 32 and "svg" not in icon
    assert len(base64.b64decode(picture["bits"])) == 360
    assert text == {"widget": "text", "text": "svg"}
    assert "svg" in args["scene"]["layout"]["cols"][0]  # the model's own arguments untouched


def test_frames_are_drawn_one_by_one_in_order():
    rule = {"deep": True, "key": "frames", "where": {"widget": "icon"}, "to": "mono",
            "width": 16, "height": 16, "as": "frames"}
    small = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
             '<circle cx="8" cy="8" r="{}" fill="white"/></svg>')
    args = {"scene": {"layout": {"widget": "icon", "frames": [small.format(3), small.format(7)]}}}
    done = _rendered({"render": {"scene": [rule]}}, args)
    frames = [base64.b64decode(f) for f in done["scene"]["layout"]["frames"]]
    assert len(frames) == 2 and all(len(f) == 32 for f in frames)
    assert sum(bin(b).count("1") for b in frames[0]) < sum(bin(b).count("1") for b in frames[1])
