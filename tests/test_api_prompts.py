"""The prompt editor edits the prompt the engine builds.

Until 13 September `/api/prompts` listed fifteen sections of a prompt the
deleted pipecat engine used to assemble, and Save wrote seven English
template files that no line of the spine read. A person could edit
"reply_rules" all evening and hear no difference, because there was none.

Now the editor describes src/spine/prompt.py: the voice rules are a file
read on every turn, the persona is rendered from identity.json, and the
rest is computed — and says so.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api import API
from src.spine import prompt as prompt_mod
from src.spine.prompt import VOICE_RULES_DEFAULT, build_system_prompt, load_voice_rules


def _request(json_data=None, key=None):
    request = MagicMock()
    request.json = AsyncMock(return_value=json_data or {})
    request.match_info = {"key": key} if key else {}
    return request


@pytest.fixture
def rules_file(tmp_path, monkeypatch):
    path = tmp_path / "prompts" / "voice_rules.txt"
    monkeypatch.setattr(prompt_mod, "voice_rules_path", lambda: path)
    return path


@pytest.fixture
def api(tmp_path):
    config = MagicMock()
    config.identity_file = tmp_path / "identity.json"
    config.spine_features = {}
    state = MagicMock()
    state.set = AsyncMock()
    return API(state, config)


# ── the file the engine reads ───────────────────────────────────────


def test_missing_or_blank_rules_file_means_the_default(rules_file) -> None:
    assert load_voice_rules() == VOICE_RULES_DEFAULT
    rules_file.parent.mkdir()
    rules_file.write_text("   \n")
    assert load_voice_rules() == VOICE_RULES_DEFAULT


def test_the_prompt_opens_with_the_saved_rules(rules_file) -> None:
    rules_file.parent.mkdir()
    rules_file.write_text("Говори як пірат.")
    assert build_system_prompt(persona="Я Дока.").startswith("Говори як пірат.\n\nЯ Дока.")


# ── the editor ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sections_are_the_spines(api, rules_file) -> None:
    resp = await api._handle_prompts(_request())
    sections = json.loads(resp.body)
    keys = [s["key"] for s in sections]
    assert keys == ["voice_rules", "persona", "role", "mcp", "memory", "exchanges", "situation"]
    by_key = {s["key"]: s for s in sections}
    assert by_key["voice_rules"]["source"] == "template"
    assert by_key["voice_rules"]["template_path"] == "prompts/voice_rules.txt"
    assert by_key["persona"]["source"] == "inline"
    assert all(s["note"] for s in sections), "every section says where it comes from"


@pytest.mark.asyncio
async def test_saving_the_rules_reaches_the_next_prompt(api, rules_file) -> None:
    resp = await api._handle_prompt_save(
        _request(json_data={"content": "Відповідай віршами."}, key="voice_rules")
    )
    body = json.loads(resp.body)
    assert body == {"ok": True, "key": "voice_rules", "char_count": 19, "live": True}
    assert rules_file.read_text(encoding="utf-8") == "Відповідай віршами."
    assert build_system_prompt().startswith("Відповідай віршами.")

    read_back = json.loads((await api._handle_prompt_section(_request(key="voice_rules"))).body)
    assert read_back["content"] == "Відповідай віршами."


@pytest.mark.asyncio
async def test_only_the_rules_are_writable(api, rules_file) -> None:
    for key in ("persona", "memory", "reply_rules"):
        resp = await api._handle_prompt_save(_request(json_data={"content": "x"}, key=key))
        assert resp.status == 400, key
        assert json.loads(resp.body)["ok"] is False


@pytest.mark.asyncio
async def test_blank_rules_are_refused(api, rules_file) -> None:
    resp = await api._handle_prompt_save(_request(json_data={"content": "  "}, key="voice_rules"))
    assert resp.status == 400
    assert not rules_file.exists()


@pytest.mark.asyncio
async def test_preview_is_the_real_builder_with_marked_examples(api, rules_file) -> None:
    rules_file.parent.mkdir()
    rules_file.write_text("Правило з файлу.")
    resp = await api._handle_prompt_preview(_request())
    text = resp.text
    assert text.startswith("Правило з файлу.")
    assert "(приклад)" in text, "the per-turn parts are shown, and shown as examples"
    assert "Що ти пам'ятаєш:" in text
