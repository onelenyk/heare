"""Tests for src/identity.py — load, validate, ensure, render, reset."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from src.identity import (
    Identity,
    _validate,
    ensure_identity,
    load_identity,
    render_persona,
    reset_identity,
)
from src.config import Settings


VALID_PAYLOAD: dict = {
    "name": "Гава",
    "creature": "owl",
    "vibe": "calm",
    "emoji": "🦉",
    "tagline": "listening from the shadows",
    "generated_at": "2024-01-01T00:00:00+00:00",
}


def test_load_identity_valid(tmp_path) -> None:
    path = tmp_path / "identity.json"
    path.write_text(json.dumps(VALID_PAYLOAD), encoding="utf-8")
    result = load_identity(path)
    assert result is not None
    assert result["name"] == "Гава"
    assert result["creature"] == "owl"
    assert result["vibe"] == "calm"
    assert result["emoji"] == "🦉"
    assert result["tagline"] == "listening from the shadows"


def test_load_identity_missing_file(tmp_path) -> None:
    result = load_identity(tmp_path / "nonexistent.json")
    assert result is None


def test_load_identity_invalid_json(tmp_path) -> None:
    path = tmp_path / "identity.json"
    path.write_text("this is not json {{{{", encoding="utf-8")
    result = load_identity(path)
    assert result is None


def test_load_identity_missing_keys(tmp_path) -> None:
    path = tmp_path / "identity.json"
    path.write_text(json.dumps({"name": "x"}), encoding="utf-8")
    result = load_identity(path)
    assert result is None


def test_validate_rejects_missing_key() -> None:
    payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "creature"}
    with pytest.raises(ValueError):
        _validate(payload)


def test_validate_accepts_complete() -> None:
    result = _validate(VALID_PAYLOAD)
    assert result["name"] == "Гава"
    assert result["creature"] == "owl"


async def test_ensure_identity_loads_existing(tmp_path) -> None:
    path = tmp_path / "identity.json"
    path.write_text(json.dumps(VALID_PAYLOAD), encoding="utf-8")

    settings = Settings()
    settings.identity_file = path

    mock_cli = AsyncMock()
    mock_cli.bootstrap_identity = AsyncMock()

    result = await ensure_identity(mock_cli, settings)
    assert result["name"] == "Гава"
    mock_cli.bootstrap_identity.assert_not_awaited()


async def test_ensure_identity_generates_new(tmp_path) -> None:
    path = tmp_path / "identity.json"
    # Do not create the file — identity missing

    settings = Settings()
    settings.identity_file = path

    mock_cli = AsyncMock()
    mock_cli.bootstrap_identity = AsyncMock(return_value=VALID_PAYLOAD)

    result = await ensure_identity(mock_cli, settings)
    assert result["name"] == "Гава"
    mock_cli.bootstrap_identity.assert_awaited_once()
    assert path.exists()
    saved = json.loads(path.read_text())
    assert saved["name"] == "Гава"


def test_render_persona_substitution() -> None:
    template = "I am {name} the {creature}"
    identity: Identity = {
        "name": "Гава",
        "creature": "owl",
        "vibe": "calm",
        "emoji": "🦉",
        "tagline": "listening",
        "generated_at": "2024-01-01T00:00:00+00:00",
    }
    result = render_persona(template, identity)
    assert result == "I am Гава the owl"


def test_reset_identity_creates_backup(tmp_path) -> None:
    path = tmp_path / "identity.json"
    path.write_text(json.dumps(VALID_PAYLOAD), encoding="utf-8")

    settings = Settings()
    settings.identity_file = path

    backup = reset_identity(settings)
    assert backup is not None
    assert backup.exists()
    assert not path.exists()
    assert "backup" in backup.name
