"""Tests for CLI argument parsing and subcommand dispatch in src/main.py."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.main import _cmd_mode, _cmd_status, _cmd_stop, build_parser, main
from src.config import Settings


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

def test_build_parser_start() -> None:
    parser = build_parser()
    args = parser.parse_args(["start"])
    assert args.cmd == "start"


def test_build_parser_stop() -> None:
    parser = build_parser()
    args = parser.parse_args(["stop"])
    assert args.cmd == "stop"


def test_build_parser_status() -> None:
    parser = build_parser()
    args = parser.parse_args(["status"])
    assert args.cmd == "status"


def test_build_parser_mode() -> None:
    parser = build_parser()
    args = parser.parse_args(["mode", "silent"])
    assert args.cmd == "mode"
    assert args.mode_name == "silent"


def test_build_parser_no_args() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


# ---------------------------------------------------------------------------
# _cmd_status
# ---------------------------------------------------------------------------

def test_cmd_status_no_pid_file(capsys) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(
            pid_file=Path(tmp) / "heare.pid",
            db_path=Path(tmp) / "heare.db",
            log_dir=Path(tmp) / "logs",
        )
        with patch("src.main.load_settings", return_value=settings):
            args = MagicMock()
            result = _cmd_status(args)
    assert result == 0
    out = capsys.readouterr().out
    assert "False" in out or "not running" in out.lower() or "running: False" in out


# ---------------------------------------------------------------------------
# _cmd_stop
# ---------------------------------------------------------------------------

def test_cmd_stop_no_pid_file(capsys) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(
            pid_file=Path(tmp) / "heare.pid",
            db_path=Path(tmp) / "heare.db",
            log_dir=Path(tmp) / "logs",
        )
        with patch("src.main.load_settings", return_value=settings):
            args = MagicMock()
            result = _cmd_stop(args)
    assert result == 0
    out = capsys.readouterr().out
    assert "not running" in out.lower()


# ---------------------------------------------------------------------------
# _cmd_mode
# ---------------------------------------------------------------------------

def test_cmd_mode_writes_file(capsys) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(
            pid_file=Path(tmp) / "heare.pid",
            db_path=Path(tmp) / "heare.db",
            log_dir=Path(tmp) / "logs",
        )

        async def _run():
            # _cmd_mode's State.init() assumes the `displays` table
            # already exists (it only ALTERs it, never CREATEs it) —
            # in the running daemon that table is owned and created by
            # TranscriptStore. Do the same real init here so this test
            # exercises the actual startup order instead of masking the
            # missing table.
            from src.store.storage import TranscriptStore

            store = TranscriptStore(settings.db_path)
            await store.init()
            await store.close()

            args = type("ns", (), {"mode_name": "silent"})()
            return await _cmd_mode(args)

        with patch("src.main.load_settings", return_value=settings):
            result = asyncio.run(_run())
    assert result == 0
    out = capsys.readouterr().out
    assert "silent" in out.lower()


def test_cmd_mode_invalid(capsys) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(
            pid_file=Path(tmp) / "heare.pid",
            db_path=Path(tmp) / "heare.db",
            log_dir=Path(tmp) / "logs",
        )
        with patch("src.main.load_settings", return_value=settings):
            args = type("ns", (), {"mode_name": "unknown"})()
            with pytest.raises(ValueError):
                asyncio.run(_cmd_mode(args))
