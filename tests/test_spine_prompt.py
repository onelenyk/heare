"""Tests for spine prompt building and persona loading."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

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

    def test_load_persona_missing_required_fields_returns_empty(
        self, tmp_path: Path
    ) -> None:
        """load_persona returns '' when name or creature is missing."""
        # Missing creature
        identity_data = {
            "name": "Гав",
            "creature": "",
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
