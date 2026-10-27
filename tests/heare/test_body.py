"""«Я і моє тіло»: one block, one form, short enough for every turn."""

from __future__ import annotations

from heare.api.registry import _now
from heare.prompt.body import MAX_CHARS, NOTHING, body

PANEL = {"name": "панель", "about": "плата на столі з OLED-екраном",
         "state": "approved", "now": "годинник", "skills": [
             {"name": "screen_info", "effect": "read",
              "about": "What is on the screen, its size, widgets, icons"},
             {"name": "show", "effect": "harmless",
              "about": "Show a few words on the OLED for some seconds"},
             {"name": "scene", "effect": "harmless",
              "about": "Put a scene on the OLED: flash for seconds, pin until cleared"}]}


def test_nothing_connected_is_said_plainly():
    assert body({}) == NOTHING


def test_a_part_says_what_it_is_what_it_shows_and_what_it_can_do():
    text = body({"p": PANEL})
    assert "- панель — плата на столі з OLED-екраном; зараз: годинник" in text
    # A reading skill keeps its name for read_body; a changing one is words.
    assert "читаю сам: What is on the screen [screen_info]" in text
    assert "через руки: Show a few words on the OLED for some s…; Put a scene on the OLED" in text
    assert "[show]" not in text


def test_an_absent_part_is_one_line_and_comes_last():
    text = body({"r": {"name": "реле", "about": "кухня", "state": "away",
                       "skills": [{"name": "on", "effect": "change"}]},
                 "p": PANEL})
    assert text.index("панель") < text.index("реле")
    assert "- реле — кухня; немає на зв'язку" in text
    assert "on" not in text.split("реле", 1)[1]


def test_many_parts_stay_within_the_budget():
    many = {str(i): {**PANEL, "name": f"тіло{i}", "about": "x" * 300}
            for i in range(30)}
    text = body(many)
    assert len(text) <= MAX_CHARS + 4 and text.endswith("- …")


def test_now_is_the_head_label_as_last_reported():
    entry = {"ui": [{"name": "sees", "head": True, "value": "тихо"}],
             "values": {"sees": "Safari"}}
    assert _now(entry) == "Safari"
    assert _now({"ui": [{"name": "x", "head": True, "value": "—"}]}) == ""
    assert _now({"ui": []}) == ""
