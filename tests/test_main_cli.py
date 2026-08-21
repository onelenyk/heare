"""Tests for CLI argument parsing and subcommand dispatch in src/main.py."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.main import _cmd_status, _cmd_stop, build_parser, main
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
# ---------------------------------------------------------------------------



