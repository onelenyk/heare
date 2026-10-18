"""The hands body: the one line it shows of the ACP wire."""

from __future__ import annotations

from heare import hands


def test_a_prompt_is_one_readable_line():
    line = hands._line("out", {
        "method": "session/prompt",
        "params": {"prompt": [{"type": "text", "text": "перевір теку"}]},
    })
    assert line == "→ prompt · перевір теку"


def test_an_update_names_what_the_agent_is_doing():
    assert hands._line("in", {"method": "session/update", "params": {
        "update": {"sessionUpdate": "tool_call", "title": "bash"}}}) == (
        "← tool_call · bash")
    assert hands._line("in", {"method": "session/update", "params": {
        "update": {"sessionUpdate": "agent_message_chunk",
                   "content": {"text": "Готово."}}}}) == (
        "← agent_message_chunk · Готово.")


def test_an_answer_is_a_short_mark():
    assert hands._line("in", {"id": 3, "result": {}}) == "← ok #3"
    assert hands._line("in", {"id": 4, "error": {"message": "no"}}) == (
        "← error · no")


def test_a_long_line_is_cut_at_the_hands_own_width():
    line = hands._line("out", {"method": "session/prompt", "params": {
        "prompt": [{"text": "я" * 200}]}})
    assert line.endswith("…") and len(line) < 90


def test_the_price_says_what_the_turn_cost():
    assert hands._price(0.000152, 39392, 1000000) == "$0.0002 · 39k / 1M"
    assert hands._price(0.0, 0, 0) == ""
    assert hands._price(0.5, 900, 200000) == "$0.5000 · 900 / 200k"
