"""Tests for src/main.py CLI commands."""
from __future__ import annotations

import asyncio
import os
from unittest.mock import Mock


def test_set_passphrase_writes_to_config(tmp_path, monkeypatch) -> None:
    """set-passphrase command writes confirmation_passphrase to config.toml."""
    monkeypatch.setattr("src.config.HEARE_HOME", tmp_path)

    from src.main import _cmd_set_wake_word

    args = Mock(word="авторизую")
    result = _cmd_set_wake_word(args)

    assert result == 0
    config_file = tmp_path / "config.toml"
    assert config_file.exists()
    content = config_file.read_text()
    assert 'confirmation_passphrase = "авторизую"' in content

    onboarding_flag = tmp_path / ".onboarded"
    assert onboarding_flag.exists()


def test_set_passphrase_rejects_empty(tmp_path, monkeypatch) -> None:
    """set-passphrase command rejects empty passphrase."""
    monkeypatch.setattr("src.config.HEARE_HOME", tmp_path)

    from src.main import _cmd_set_wake_word

    args = Mock(word="  ")
    result = _cmd_set_wake_word(args)

    assert result == 1


def test_set_passphrase_updates_existing_config(tmp_path, monkeypatch) -> None:
    """set-passphrase command updates existing confirmation_passphrase line."""
    monkeypatch.setattr("src.config.HEARE_HOME", tmp_path)

    config_file = tmp_path / "config.toml"
    config_file.write_text('confirmation_passphrase = "old"\nmode = "ambient"\n')

    from src.main import _cmd_set_wake_word

    args = Mock(word="newpass")
    result = _cmd_set_wake_word(args)

    assert result == 0
    content = config_file.read_text()
    assert 'confirmation_passphrase = "newpass"' in content
    assert '"old"' not in content
    assert 'mode = "ambient"' in content


def test_multiple_daemon_instances_prevented(tmp_path, monkeypatch) -> None:
    """Starting a second daemon instance fails gracefully when first is running."""
    monkeypatch.setattr("src.config.HEARE_HOME", tmp_path)

    pid_file = tmp_path / "heare.pid"
    pid_file.write_text(str(os.getpid()))

    from src.main import _cmd_start

    args = Mock()
    result = asyncio.run(_cmd_start(args))

    assert result == 1
    assert pid_file.exists()
