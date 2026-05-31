"""Tests for src.watch.models — hardcoded list, persistence, custom add."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from src.config import Mode, Settings
from src.watch import models


def test_default_model_per_provider() -> None:
    assert models.DEFAULT_MODEL["deepseek"] in models.PROVIDERS["deepseek"].model_whitelist
    assert models.DEFAULT_MODEL["zai"] in models.PROVIDERS["zai"].model_whitelist


def test_read_current_model_returns_provider_default_when_unset(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        monkeypatch.setattr(Path, "home", lambda: base)
        settings = Settings(
            pid_file=base / "heare.pid",
            db_path=base / "heare.db",
            log_dir=base / "logs",
            mode=Mode.AMBIENT,
        )
        assert models.read_current_model(settings, "deepseek") == models.DEFAULT_MODEL["deepseek"]
        assert models.read_current_model(settings, "zai") == models.DEFAULT_MODEL["zai"]


def test_write_then_read_round_trips(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        monkeypatch.setattr(Path, "home", lambda: base)
        settings = Settings(
            pid_file=base / "heare.pid",
            db_path=base / "heare.db",
            log_dir=base / "logs",
            mode=Mode.AMBIENT,
        )
        models.write_current_model(settings, "anthropic/claude-haiku-4.5")
        assert models.read_current_model(settings, "deepseek") == "anthropic/claude-haiku-4.5"


def test_add_custom_model_appears_in_models_for_provider(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        monkeypatch.setattr(Path, "home", lambda: base)
        settings = Settings(
            pid_file=base / "heare.pid",
            db_path=base / "heare.db",
            log_dir=base / "logs",
            mode=Mode.AMBIENT,
        )
        models.add_custom_model(settings, "deepseek", "fictional/cool-model-7b")
        result = models.models_for_provider(settings, "deepseek")
        assert "fictional/cool-model-7b" in result
        assert result[0] in models.PROVIDERS["deepseek"].model_whitelist  # whitelist entries come first


def test_add_custom_model_dedupes(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        monkeypatch.setattr(Path, "home", lambda: base)
        settings = Settings(
            pid_file=base / "heare.pid",
            db_path=base / "heare.db",
            log_dir=base / "logs",
            mode=Mode.AMBIENT,
        )
        models.add_custom_model(settings, "deepseek", "fictional/x")
        models.add_custom_model(settings, "deepseek", "fictional/x")
        data = json.loads(models.custom_models_file(settings).read_text())
        assert data["deepseek"].count("fictional/x") == 1


def test_add_blank_model_is_noop(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        monkeypatch.setattr(Path, "home", lambda: base)
        settings = Settings(
            pid_file=base / "heare.pid",
            db_path=base / "heare.db",
            log_dir=base / "logs",
            mode=Mode.AMBIENT,
        )
        models.add_custom_model(settings, "deepseek", "   ")
        assert not models.custom_models_file(settings).exists()


def test_models_for_provider_dedupes_overlap_with_hardcoded(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        monkeypatch.setattr(Path, "home", lambda: base)
        settings = Settings(
            pid_file=base / "heare.pid",
            db_path=base / "heare.db",
            log_dir=base / "logs",
            mode=Mode.AMBIENT,
        )
        # Add a model id that already exists in the whitelist
        models.add_custom_model(settings, "deepseek", models.PROVIDERS["deepseek"].model_whitelist[0])
        result = models.models_for_provider(settings, "deepseek")
        assert result.count(models.PROVIDERS["deepseek"].model_whitelist[0]) == 1
