"""Tests for spine prompt building and persona loading."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from src.spine.prompt import build_system_prompt, load_persona


class TestBuildSystemPrompt:
    """Test build_system_prompt function."""

    def test_bare_call_contains_voice_rules(self) -> None:
        """Bare call returns prompt with voice rules and nothing else."""
        prompt = build_system_prompt()
        assert "Ти голосовий асистент на ім'я heare" in prompt
        assert "Що ти пам'ятаєш:" not in prompt
        assert "Останні розмови:" not in prompt
        # Should be just the voice rules, nothing else
        expected = (
            "Ти голосовий асистент на ім'я heare. Тебе слухають вухами, а не "
            "читають: відповідай коротко, українською, простою прозою — без "
            "розмітки, списків і коду. Одна-три фрази, як у живій розмові."
        )
        assert prompt == expected

    def test_persona_appears_when_nonempty(self) -> None:
        """Persona section appears only when non-empty."""
        prompt = build_system_prompt(persona="Я веселий помічник.")
        assert "Я веселий помічник." in prompt
        assert "Що ти пам'ятаєш:" not in prompt

    def test_persona_not_appears_when_empty(self) -> None:
        """Persona section does not appear when empty."""
        prompt = build_system_prompt(persona="")
        assert "Що ти пам'ятаєш:" not in prompt
        # Just voice rules
        assert prompt.count("\n\n") == 0

    def test_memory_appears_with_header(self) -> None:
        """Memory block appears with Ukrainian header when non-empty."""
        prompt = build_system_prompt(memory_block="Користувач любить музику.")
        assert "Що ти пам'ятаєш:" in prompt
        assert "Користувач любить музику." in prompt

    def test_memory_not_appears_when_empty(self) -> None:
        """Memory block does not appear when empty."""
        prompt = build_system_prompt(memory_block="")
        assert "Що ти пам'ятаєш:" not in prompt

    def test_exchanges_appear_with_header(self) -> None:
        """Exchanges appear with Ukrainian header when non-empty."""
        exchanges = [
            {"user": "Привіт!", "agent": "Привіт! Як ти?"},
        ]
        prompt = build_system_prompt(exchanges=exchanges)
        assert "Останні розмови:" in prompt
        assert "Користувач: Привіт!" in prompt
        assert "Ти: Привіт! Як ти?" in prompt

    def test_exchanges_not_appear_when_empty(self) -> None:
        """Exchanges section does not appear when list is empty or None."""
        prompt1 = build_system_prompt(exchanges=[])
        assert "Останні розмови:" not in prompt1

        prompt2 = build_system_prompt(exchanges=None)
        assert "Останні розмови:" not in prompt2

    def test_exchanges_render_in_order(self) -> None:
        """Exchanges render user then agent, in order."""
        exchanges = [
            {"user": "Скажи привіт", "agent": "Привіт!"},
            {"user": "А як ти?", "agent": "Я гарно."},
        ]
        prompt = build_system_prompt(exchanges=exchanges)
        lines = prompt.split("\n")
        # Find the exchanges section
        idx_exchanges = next(
            i for i, line in enumerate(lines) if "Останні розмови:" in line
        )
        # Check order after the header
        exchange_lines = [line for line in lines[idx_exchanges + 1 :] if line.strip()]
        # Should be: Користувач: ..., Ти: ..., Користувач: ..., Ти: ...
        assert "Користувач: Скажи привіт" in exchange_lines[0]
        assert "Ти: Привіт!" in exchange_lines[1]
        assert "Користувач: А як ти?" in exchange_lines[2]
        assert "Ти: Я гарно." in exchange_lines[3]

    def test_exchanges_truncated_at_200_chars(self) -> None:
        """Each exchange line is capped at 200 chars."""
        long_user = "a" * 250
        long_agent = "b" * 250
        exchanges = [
            {"user": long_user, "agent": long_agent},
        ]
        prompt = build_system_prompt(exchanges=exchanges)
        lines = prompt.split("\n")
        for line in lines:
            if line.startswith("Користувач:"):
                # Should be truncated
                assert len(line) <= len("Користувач: ") + 200
            elif line.startswith("Ти:"):
                # Should be truncated
                assert len(line) <= len("Ти: ") + 200

    def test_now_renders_date_line(self) -> None:
        """Current date/time renders a date line when now is given."""
        dt = datetime(2026, 8, 12, 14, 30, 45)
        prompt = build_system_prompt(now=dt)
        assert "Зараз: 2026-08-12 14:30:45" in prompt

    def test_now_not_appears_when_none(self) -> None:
        """Date line does not appear when now is None."""
        prompt = build_system_prompt(now=None)
        assert "Зараз:" not in prompt

    def test_determinism(self) -> None:
        """Same inputs produce identical output."""
        dt = datetime(2026, 8, 12, 14, 30, 45)
        exchanges = [
            {"user": "Привіт", "agent": "Привіт!"},
        ]
        prompt1 = build_system_prompt(
            persona="Я веселий",
            memory_block="Люблю музику",
            exchanges=exchanges,
            now=dt,
        )
        prompt2 = build_system_prompt(
            persona="Я веселий",
            memory_block="Люблю музику",
            exchanges=exchanges,
            now=dt,
        )
        assert prompt1 == prompt2

    def test_all_sections_present(self) -> None:
        """All sections appear when all inputs are provided."""
        dt = datetime(2026, 8, 12, 14, 30, 45)
        exchanges = [
            {"user": "Привіт", "agent": "Привіт!"},
        ]
        prompt = build_system_prompt(
            persona="Я веселий",
            memory_block="Люблю музику",
            exchanges=exchanges,
            now=dt,
        )
        # Check that all sections are present
        assert "Ти голосовий асистент" in prompt
        assert "Я веселий" in prompt
        assert "Що ти пам'ятаєш:" in prompt
        assert "Люблю музику" in prompt
        assert "Останні розмови:" in prompt
        assert "Користувач: Привіт" in prompt
        assert "Ти: Привіт!" in prompt
        assert "Зараз: 2026-08-12 14:30:45" in prompt

    def test_static_prefix_is_stable_across_dynamic_changes(self) -> None:
        """The static block (voice rules + persona) is a stable prefix.

        For DeepSeek's prefix cache to hit, the leading bytes of the prompt
        must be identical across turns whenever persona is unchanged, no
        matter what memory/exchanges/now vary to. The static-only prompt
        (no dynamic args) gives the exact static text explicitly.
        """
        persona = "Я веселий помічник, мій стиль — теплий."
        static_only = build_system_prompt(persona=persona)

        prompt_a = build_system_prompt(
            persona=persona,
            memory_block="Користувач любить музику.",
            exchanges=[{"user": "Привіт", "agent": "Привіт!"}],
            now=datetime(2026, 8, 12, 10, 0, 0),
        )
        prompt_b = build_system_prompt(
            persona=persona,
            memory_block="Зовсім інша інформація про щось інше.",
            exchanges=[{"user": "Як справи?", "agent": "Добре, дякую."}],
            now=datetime(2026, 8, 13, 23, 59, 59),
        )

        # Both full prompts must start with exactly the static text.
        assert prompt_a.startswith(static_only)
        assert prompt_b.startswith(static_only)

        # The common string prefix of the two must be at least as long as
        # the static block — i.e. dynamic changes cannot bleed backward
        # into the cached prefix.
        common_prefix_len = len(os.path.commonprefix([prompt_a, prompt_b]))
        assert common_prefix_len >= len(static_only)

    def test_time_line_is_last(self) -> None:
        """With all sections present, the date/time line is the final line.

        Time changes on every single turn, so it must sit deepest in the
        prompt to keep as much of the prefix cacheable as possible.
        """
        dt = datetime(2026, 8, 12, 14, 30, 45)
        exchanges = [
            {"user": "Привіт", "agent": "Привіт!"},
            {"user": "А як ти?", "agent": "Я гарно."},
        ]
        prompt = build_system_prompt(
            persona="Я веселий",
            memory_block="Люблю музику",
            exchanges=exchanges,
            now=dt,
        )
        lines = [line for line in prompt.split("\n") if line.strip()]
        assert lines[-1] == "Зараз: 2026-08-12 14:30:45"


class TestLoadPersona:
    """Test load_persona function."""

    def test_load_persona_from_existing_file(self, tmp_path: Path) -> None:
        """load_persona reads an existing identity file."""
        # Create a fake identity file
        identity_data = {
            "name": "Гав",
            "creature": "собака",
            "vibe": "веселий",
            "emoji": "🐕",
            "tagline": "вереск і радість",
            "generated_at": "2026-08-01T12:00:00Z",
        }
        identity_file = tmp_path / "identity.json"
        identity_file.write_text(json.dumps(identity_data, ensure_ascii=False))

        # Create a mock settings object
        class MockSettings:
            def __init__(self, path: Path) -> None:
                self.identity_file = path

        settings = MockSettings(identity_file)
        persona = load_persona(settings)

        # Check that persona was loaded and formatted
        assert "Я на ім'я Гав" in persona
        assert "собака" in persona
        assert "веселий" in persona
        assert "🐕" in persona

    def test_load_persona_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """load_persona returns '' when file is missing."""

        class MockSettings:
            def __init__(self, path: Path) -> None:
                self.identity_file = path

        settings = MockSettings(tmp_path / "nonexistent.json")
        persona = load_persona(settings)
        assert persona == ""

    def test_load_persona_invalid_json_returns_empty(self, tmp_path: Path) -> None:
        """load_persona returns '' on JSON parse error."""
        identity_file = tmp_path / "identity.json"
        identity_file.write_text("not valid json {")

        class MockSettings:
            def __init__(self, path: Path) -> None:
                self.identity_file = path

        settings = MockSettings(identity_file)
        persona = load_persona(settings)
        assert persona == ""

    def test_load_persona_missing_name_returns_empty(self, tmp_path: Path) -> None:
        """load_persona returns '' when the name is missing.

        The name is the one field that is pure identity; without it there is
        nothing to introduce.
        """
        identity_data = {
            "name": "",
            "creature": "собака",
            "vibe": "веселий",
            "emoji": "🐕",
            "tagline": "вереск і радість",
            "generated_at": "2026-08-01T12:00:00Z",
        }
        identity_file = tmp_path / "identity.json"
        identity_file.write_text(json.dumps(identity_data, ensure_ascii=False))

        class MockSettings:
            def __init__(self, path: Path) -> None:
                self.identity_file = path

        settings = MockSettings(identity_file)
        persona = load_persona(settings)
        assert persona == ""

    def test_load_persona_name_only_still_renders(self, tmp_path: Path) -> None:
        """An identity file with nothing but a name still renders sanely."""
        identity_file = tmp_path / "identity.json"
        identity_file.write_text(json.dumps({"name": "Гав"}, ensure_ascii=False))

        class MockSettings:
            def __init__(self, path: Path) -> None:
                self.identity_file = path

        persona = load_persona(MockSettings(identity_file))

        assert "Я на ім'я Гав" in persona
        # The division of labour is fixed text, not read from the file.
        assert "робітник" in persona
        assert "озиваюсь" in persona
        assert persona.endswith(".")
        # No dangling "мій стиль —" with nothing after it.
        assert "стиль" not in persona

    def test_load_persona_no_identity_file_attr(self, tmp_path: Path) -> None:
        """load_persona falls back to ~/.heare/identity.json when attr missing."""
        # Create .heare directory structure
        heare_home = tmp_path / ".heare"
        heare_home.mkdir()
        identity_file = heare_home / "identity.json"

        identity_data = {
            "name": "Гав",
            "creature": "собака",
            "vibe": "веселий",
            "emoji": "🐕",
            "tagline": "вереск і радість",
            "generated_at": "2026-08-01T12:00:00Z",
        }
        identity_file.write_text(json.dumps(identity_data, ensure_ascii=False))

        # Mock settings without identity_file attribute
        class MockSettings:
            pass

        # Temporarily patch Path.home() for this test
        import unittest.mock

        with unittest.mock.patch("pathlib.Path.home", return_value=tmp_path):
            settings = MockSettings()
            persona = load_persona(settings)

        # Should have loaded from the fallback path
        assert "Я на ім'я Гав" in persona

    def test_load_persona_no_vibe_no_style_clause(self, tmp_path: Path) -> None:
        """An English vibe with no known traits leaves no English behind."""
        identity_file = tmp_path / "identity.json"
        identity_file.write_text(
            json.dumps(
                {"name": "Гав", "vibe": "quixotic, antediluvian"},
                ensure_ascii=False,
            )
        )

        class MockSettings:
            def __init__(self, path: Path) -> None:
                self.identity_file = path

        persona = load_persona(MockSettings(identity_file))
        assert "quixotic" not in persona
        assert "antediluvian" not in persona

    def test_load_persona_minimal_fields(self, tmp_path: Path) -> None:
        """load_persona works with minimal required fields."""
        identity_data = {
            "name": "Гав",
            "creature": "собака",
        }
        identity_file = tmp_path / "identity.json"
        identity_file.write_text(json.dumps(identity_data, ensure_ascii=False))

        class MockSettings:
            def __init__(self, path: Path) -> None:
                self.identity_file = path

        settings = MockSettings(identity_file)
        persona = load_persona(settings)

        # Should have name and creature, but not vibe or emoji
        assert "Я на ім'я Гав" in persona
        assert "собака" in persona
        assert persona.endswith(".")


# The identity actually sitting in ~/.heare/identity.json on the dev machine
# (2026-06-07 generation). Inlined so the test does not depend on the box it
# runs on. Its `creature` is an English capability advertisement: everything
# after the name is false for the live engine, which has three verbs
# (delegate / remember / recall), cannot execute code or browse, and never
# speaks unprompted.
BOILERPLATE_IDENTITY = {
    "name": "Doka",
    "creature": (
        "A capable ambient AI that lives in your headphones, quietly "
        "listening, and acts as your all-purpose terminal, browser, and "
        "tool system — executing code, searching the web, and automating "
        "tasks on its own initiative."
    ),
    "vibe": "curious, pragmatic, efficient",
    "emoji": "🎧",
    "tagline": "Слухаю, розумію, роблю.",
    "generated_at": "2026-06-07T21:42:04.522677+00:00",
}

# Words whose presence in the prompt would tell the voice model it is a
# terminal / a browser / a self-starter.
FALSE_CLAIM_WORDS = (
    "terminal",
    "термінал",
    "browser",
    "браузер",
    "executing code",
    "виконує код",
    "searching the web",
    "automating",
    "on its own initiative",
    "ініціатив",
    "tool system",
)


def _write_identity(tmp_path: Path, data: dict) -> object:
    identity_file = tmp_path / "identity.json"
    identity_file.write_text(json.dumps(data, ensure_ascii=False))

    class MockSettings:
        def __init__(self, path: Path) -> None:
            self.identity_file = path

    return MockSettings(identity_file)


def _cyrillic_share(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if "Ѐ" <= ch <= "ӿ") / len(letters)


class TestPersonaIsAVoiceNotATerminal:
    """The persona says who the assistant is, never what it can do."""

    def test_capability_boilerplate_makes_no_false_claims(
        self, tmp_path: Path
    ) -> None:
        """The real boilerplate identity yields no terminal/browser/initiative."""
        persona = load_persona(_write_identity(tmp_path, BOILERPLATE_IDENTITY))

        assert persona  # it still renders a persona
        lowered = persona.lower()
        for word in FALSE_CLAIM_WORDS:
            assert word not in lowered, f"persona still claims: {word}"

    def test_false_claims_absent_from_the_whole_prompt(self, tmp_path: Path) -> None:
        """Nothing downstream of load_persona reintroduces the claims."""
        persona = load_persona(_write_identity(tmp_path, BOILERPLATE_IDENTITY))
        prompt = build_system_prompt(
            persona=persona,
            memory_block="Користувач любить музику.",
            exchanges=[{"user": "Привіт", "agent": "Привіт!"}],
            now=datetime(2026, 8, 12, 14, 30, 45),
        )
        lowered = prompt.lower()
        for word in FALSE_CLAIM_WORDS:
            assert word not in lowered, f"prompt still claims: {word}"

    def test_boilerplate_creature_sentence_is_dropped_whole(
        self, tmp_path: Path
    ) -> None:
        """A capability blurb is dropped entirely, not trimmed."""
        persona = load_persona(_write_identity(tmp_path, BOILERPLATE_IDENTITY))
        assert "ambient" not in persona.lower()
        assert "headphones" not in persona.lower()
        # The name survives — it is the one field that is pure identity.
        assert "Doka" in persona

    def test_rendered_persona_is_ukrainian(self, tmp_path: Path) -> None:
        """The persona is Ukrainian prose, matching the voice rules above it."""
        persona = load_persona(_write_identity(tmp_path, BOILERPLATE_IDENTITY))
        # 'Doka' is a name and stays Latin; everything else is Cyrillic.
        assert _cyrillic_share(persona) > 0.9

    def test_persona_states_the_true_division_of_labour(
        self, tmp_path: Path
    ) -> None:
        """It speaks, hands work to its worker, and answers when addressed."""
        persona = load_persona(_write_identity(tmp_path, BOILERPLATE_IDENTITY))
        assert "голос" in persona
        assert "робітник" in persona
        assert "озиваюсь" in persona
        assert "не починаю" in persona

    def test_character_creature_survives(self, tmp_path: Path) -> None:
        """A short Ukrainian character phrase is kept as written."""
        identity = dict(BOILERPLATE_IDENTITY)
        identity["creature"] = "спокійний голос у навушниках"
        persona = load_persona(_write_identity(tmp_path, identity))
        assert "спокійний голос у навушниках" in persona

    def test_english_vibe_is_rendered_in_ukrainian(self, tmp_path: Path) -> None:
        """The English trait list does not survive into Ukrainian prose."""
        persona = load_persona(_write_identity(tmp_path, BOILERPLATE_IDENTITY))
        assert "curious" not in persona
        assert "pragmatic" not in persona
        assert "efficient" not in persona
        assert "допитливий" in persona

    def test_persona_is_deterministic(self, tmp_path: Path) -> None:
        """Same file -> same bytes, or the prefix cache never hits."""
        settings = _write_identity(tmp_path, BOILERPLATE_IDENTITY)
        assert load_persona(settings) == load_persona(settings)

    def test_real_persona_stays_in_the_static_prefix(self, tmp_path: Path) -> None:
        """The rendered persona is part of the stable, cacheable head."""
        persona = load_persona(_write_identity(tmp_path, BOILERPLATE_IDENTITY))
        static_only = build_system_prompt(persona=persona)

        prompt_a = build_system_prompt(
            persona=persona,
            memory_block="Користувач любить музику.",
            exchanges=[{"user": "Привіт", "agent": "Привіт!"}],
            now=datetime(2026, 8, 12, 10, 0, 0),
        )
        prompt_b = build_system_prompt(
            persona=persona,
            memory_block="Зовсім інша інформація про щось інше.",
            exchanges=[{"user": "Як справи?", "agent": "Добре."}],
            now=datetime(2026, 8, 13, 23, 59, 59),
        )

        assert prompt_a.startswith(static_only)
        assert prompt_b.startswith(static_only)
        assert len(os.path.commonprefix([prompt_a, prompt_b])) >= len(static_only)

    def test_missing_file_still_yields_empty(self, tmp_path: Path) -> None:
        """A missing identity file yields '' — no invented persona."""

        class MockSettings:
            identity_file = tmp_path / "nope.json"

        assert load_persona(MockSettings()) == ""


def test_the_persona_does_not_promise_silence_it_will_not_keep(tmp_path) -> None:
    """«Сам розмову не починаю» was written as a constant because no
    identity could falsify it. A feature switch did: `repeats` and
    `watcher` went on and the assistant spent an afternoon introducing
    itself as something that never speaks first while the engine was
    arranging to do exactly that."""
    import json
    from types import SimpleNamespace

    from src.spine.prompt import load_persona

    identity = tmp_path / "identity.json"
    identity.write_text(json.dumps({"name": "Дока"}), encoding="utf-8")
    settings = SimpleNamespace(identity_file=identity)

    quiet = load_persona(settings, speaks_first=False)
    talks = load_persona(settings, speaks_first=True)

    assert "Сам розмову не починаю" in quiet
    assert "Сам розмову не починаю" not in talks
    assert "зрідка кажу щось сам" in talks
