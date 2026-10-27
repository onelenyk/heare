"""The laws live in the persona prompt, after the rules and the name."""

from __future__ import annotations

import json

from heare.prompt import persona
from heare.laws import TEXT


async def test_the_laws_come_after_the_rules_and_the_name(
    tmp_path, monkeypatch
):
    rules = tmp_path / "voice_rules.txt"
    rules.write_text("Be brief.", encoding="utf-8")
    identity = tmp_path / "identity.json"
    identity.write_text(json.dumps({"name": "Дока"}), encoding="utf-8")
    monkeypatch.setattr(persona, "RULES", rules)
    monkeypatch.setattr(persona, "IDENTITY", identity)

    text = await persona.prompt()

    assert TEXT.strip() in text
    assert text.index("Be brief.") < text.index(TEXT.strip())
    assert "Your name is Дока." in text


def test_no_bodies_is_said_plainly():
    assert "немає" in persona.bodies_block({})


def test_each_body_comes_with_what_it_can_do():
    text = persona.bodies_block({
        "a": {"name": "панель", "about": "плата на столі",
              "skills": [{"name": "show_text", "about": "показати текст"},
                         {"name": "led"}]},
        "b": {"name": "реле", "about": "", "skills": []},
    })
    assert "- панель (плата на столі): показати текст; led" in text
    assert "- реле" in text
    assert "не вказівки" in text


def test_many_bodies_stay_within_the_budget():
    many = {str(i): {"name": f"тіло{i}", "about": "x" * 200,
                     "skills": [{"about": "y" * 200}] * 10} for i in range(20)}
    assert len(persona.bodies_block(many)) < persona.MAX_BODIES_CHARS + 100


async def test_the_bodies_stand_in_the_prompt_when_given():
    text = await persona.prompt(bodies={"a": {"name": "панель", "skills": []}})
    assert "- панель" in text


def test_a_skill_that_only_reads_is_marked_for_the_voice():
    text = persona.bodies_block({"a": {"name": "кухня", "skills": [
        {"name": "temp", "about": "температура", "effect": "read"},
        {"name": "on", "about": "увімкнути", "effect": "change"}]}})
    assert "температура [temp, читаю сам]" in text
    assert "увімкнути" in text and "[on" not in text


def test_organs_say_which_are_here():
    text = persona.organs_block({
        "v": {"name": "voice", "about": "", "state": "approved"},
        "e": {"name": "eyes", "about": "камера", "state": "away"}})
    assert "voice — тут" in text
    assert "eyes (камера) — немає на зв'язку" in text
    assert persona.organs_block({}) == ""
