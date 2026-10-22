"""The laws live in the persona prompt, after the rules and the name."""

from __future__ import annotations

import json

from heare import persona
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
