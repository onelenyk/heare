"""Tests for src.watch.models — hardcoded list, persistence, custom add."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from src.config import Mode, Settings
from src.watch import models


def _make_settings(tmp: str) -> Settings:
    base = Path(tmp)
    return Settings(
        pid_file=base / "heare.pid",
        db_path=base / "heare.db",
        mode_file=base / "mode",
        log_dir=base / "logs",
        mode=Mode.AMBIENT,
        mute_file=base / "mute.bot",
        mute_input_file=base / "mute.input",
        provider_file=base / "provider",
        identity_file=base / "identity.json",
        inject_dir=base / "inject",
        speakers_file=base / "speakers.json",
    )


def test_default_model_per_provider() -> None:
    assert models.DEFAULT_MODEL["openrouter"] in models.OPENROUTER_MODELS
    assert models.DEFAULT_MODEL["zai"] in models.ZAI_MODELS


def test_read_current_model_returns_provider_default_when_unset() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        assert models.read_current_model(settings, "openrouter") == models.DEFAULT_MODEL["openrouter"]
        assert models.read_current_model(settings, "zai") == models.DEFAULT_MODEL["zai"]


def test_write_then_read_round_trips() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        models.write_current_model(settings, "anthropic/claude-haiku-4.5")
        assert models.read_current_model(settings, "openrouter") == "anthropic/claude-haiku-4.5"


def test_add_custom_model_appears_in_models_for_provider() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        models.add_custom_model(settings, "openrouter", "fictional/cool-model-7b")
        result = models.models_for_provider(settings, "openrouter")
        assert "fictional/cool-model-7b" in result
        assert result[0] in models.OPENROUTER_MODELS  # hardcoded entries come first


def test_add_custom_model_dedupes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        models.add_custom_model(settings, "openrouter", "fictional/x")
        models.add_custom_model(settings, "openrouter", "fictional/x")
        data = json.loads(models.custom_models_file(settings).read_text())
        assert data["openrouter"].count("fictional/x") == 1


def test_add_blank_model_is_noop() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        models.add_custom_model(settings, "openrouter", "   ")
        assert not models.custom_models_file(settings).exists()


def test_models_for_provider_dedupes_overlap_with_hardcoded() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = _make_settings(tmp)
        # Add a model id that already exists in the hardcoded list
        models.add_custom_model(settings, "openrouter", models.OPENROUTER_MODELS[0])
        result = models.models_for_provider(settings, "openrouter")
        assert result.count(models.OPENROUTER_MODELS[0]) == 1
