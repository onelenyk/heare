"""Tests for CLI argument parsing and subcommand dispatch in src/main.py."""
from __future__ import annotations

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


def test_build_parser_enroll_owner_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args(["enroll-owner"])
    assert args.cmd == "enroll-owner"
    assert args.duration == 15
    assert args.label == "owner"


def test_build_parser_enroll_owner_custom() -> None:
    parser = build_parser()
    args = parser.parse_args(["enroll-owner", "--duration", "10", "--label", "Nazar"])
    assert args.cmd == "enroll-owner"
    assert args.duration == 10
    assert args.label == "Nazar"


def test_cmd_enroll_owner_requires_flag_enabled(monkeypatch, capsys) -> None:
    from src.main import _cmd_enroll_owner

    monkeypatch.setattr(
        "src.main.load_settings",
        lambda: Settings(),  # speaker_id_enabled defaults to False
    )
    ns = type("ns", (), {"duration": 15, "label": "owner"})()
    rc = _cmd_enroll_owner(ns)
    assert rc == 1
    out = capsys.readouterr().out
    assert "speaker_id_enabled is False" in out


# ---------------------------------------------------------------------------
# _cmd_status
# ---------------------------------------------------------------------------

def test_cmd_status_no_pid_file(capsys) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(
            pid_file=Path(tmp) / "heare.pid",
            db_path=Path(tmp) / "heare.db",
            mode_file=Path(tmp) / "mode",
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
            mode_file=Path(tmp) / "mode",
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
        mode_file = Path(tmp) / "mode"
        settings = Settings(
            pid_file=Path(tmp) / "heare.pid",
            db_path=Path(tmp) / "heare.db",
            mode_file=mode_file,
            log_dir=Path(tmp) / "logs",
        )
        with patch("src.main.load_settings", return_value=settings):
            args = MagicMock()
            args.mode_name = "silent"
            result = _cmd_mode(args)
        assert result == 0
        assert mode_file.exists()
        assert mode_file.read_text().strip() == "silent"


# ---------------------------------------------------------------------------
# main dispatch
# ---------------------------------------------------------------------------

def test_main_dispatches_to_func(capsys) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(
            pid_file=Path(tmp) / "heare.pid",
            db_path=Path(tmp) / "heare.db",
            mode_file=Path(tmp) / "mode",
            log_dir=Path(tmp) / "logs",
        )
        with patch("src.main.load_settings", return_value=settings):
            result = main(["status"])
    assert result == 0


# ---------------------------------------------------------------------------
# SPK-A2: speakers list / info / rm / rename CLI
# ---------------------------------------------------------------------------


def _speakers_tmp_settings(tmp: str) -> Settings:
    return Settings(
        pid_file=Path(tmp) / "heare.pid",
        db_path=Path(tmp) / "heare.db",
        mode_file=Path(tmp) / "mode",
        log_dir=Path(tmp) / "logs",
        speakers_file=Path(tmp) / "speakers.json",
    )


def test_speakers_list_empty(capsys) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = _speakers_tmp_settings(tmp)
        with patch("src.main.load_settings", return_value=settings):
            rc = main(["speakers", "list"])
    assert rc == 0
    assert "no speakers enrolled" in capsys.readouterr().out


def test_speakers_list_prints_all(capsys) -> None:
    import numpy as np

    from src.speaker_gallery import SpeakerGallery

    with tempfile.TemporaryDirectory() as tmp:
        settings = _speakers_tmp_settings(tmp)
        g = SpeakerGallery(settings.speakers_file)
        g.enroll_owner(np.ones(5, dtype=np.float32), label="Nazar")
        with patch("src.main.load_settings", return_value=settings):
            rc = main(["speakers", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "owner" in out
    assert "Nazar" in out
    assert "turn_count=" in out


def test_speakers_info_prints_fields(capsys) -> None:
    import numpy as np

    from src.speaker_gallery import SpeakerGallery

    with tempfile.TemporaryDirectory() as tmp:
        settings = _speakers_tmp_settings(tmp)
        g = SpeakerGallery(settings.speakers_file)
        g.enroll_owner(np.ones(5, dtype=np.float32), label="Nazar")
        with patch("src.main.load_settings", return_value=settings):
            rc = main(["speakers", "info", "owner"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "id: owner" in out
    assert "label: Nazar" in out
    assert "ref_count:" in out
    assert "centroid_norm:" in out


def test_speakers_info_missing_returns_1(capsys) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = _speakers_tmp_settings(tmp)
        with patch("src.main.load_settings", return_value=settings):
            rc = main(["speakers", "info", "ghost"])
    assert rc == 1
    assert "speaker not found" in capsys.readouterr().out


def test_speakers_rm_requires_yes_flag(capsys) -> None:
    import numpy as np

    from src.speaker_gallery import SpeakerGallery

    with tempfile.TemporaryDirectory() as tmp:
        settings = _speakers_tmp_settings(tmp)
        g = SpeakerGallery(settings.speakers_file)
        g.enroll_owner(np.ones(5, dtype=np.float32))
        with patch("src.main.load_settings", return_value=settings):
            rc = main(["speakers", "rm", "owner"])
        # Owner must still be in gallery — no --yes, no removal
        g2 = SpeakerGallery.load(settings.speakers_file)
        assert "owner" in g2.list_speakers()
    assert rc == 1
    assert "pass --yes" in capsys.readouterr().out


def test_speakers_rm_with_yes_removes(capsys) -> None:
    import numpy as np

    from src.speaker_gallery import SpeakerGallery

    with tempfile.TemporaryDirectory() as tmp:
        settings = _speakers_tmp_settings(tmp)
        g = SpeakerGallery(settings.speakers_file)
        g.enroll_owner(np.ones(5, dtype=np.float32))
        with patch("src.main.load_settings", return_value=settings):
            rc = main(["speakers", "rm", "owner", "--yes"])
        g2 = SpeakerGallery.load(settings.speakers_file)
        assert g2.list_speakers() == []
    assert rc == 0
    assert "removed: owner" in capsys.readouterr().out


def test_speakers_rename_success(capsys) -> None:
    import numpy as np

    from src.speaker_gallery import SpeakerGallery

    with tempfile.TemporaryDirectory() as tmp:
        settings = _speakers_tmp_settings(tmp)
        g = SpeakerGallery(settings.speakers_file)
        g.enroll_owner(np.ones(5, dtype=np.float32), label="owner")
        with patch("src.main.load_settings", return_value=settings):
            rc = main(["speakers", "rename", "owner", "Nazar"])
        g2 = SpeakerGallery.load(settings.speakers_file)
        assert g2.get_label("owner") == "Nazar"
    assert rc == 0
    assert "renamed" in capsys.readouterr().out


def test_speakers_rename_invalid_label(capsys) -> None:
    import numpy as np

    from src.speaker_gallery import SpeakerGallery

    with tempfile.TemporaryDirectory() as tmp:
        settings = _speakers_tmp_settings(tmp)
        g = SpeakerGallery(settings.speakers_file)
        g.enroll_owner(np.ones(5, dtype=np.float32))
        with patch("src.main.load_settings", return_value=settings):
            rc = main(["speakers", "rename", "owner", "bad\nlabel"])
    assert rc == 1
    assert "invalid label" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# SPK-A3: speakers audit CLI
# ---------------------------------------------------------------------------


def test_speakers_audit_prints_ok_for_healthy(capsys) -> None:
    import numpy as np

    from src.speaker_gallery import SpeakerGallery

    with tempfile.TemporaryDirectory() as tmp:
        settings = _speakers_tmp_settings(tmp)
        g = SpeakerGallery(settings.speakers_file)
        g.enroll_owner(np.array([1.0, 0.0, 0.0], dtype=np.float32))
        with patch("src.main.load_settings", return_value=settings):
            rc = main(["speakers", "audit"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "owner" in out
    assert "OK" in out


def test_speakers_audit_prints_drift_marker(capsys) -> None:
    import numpy as np

    from src.speaker_gallery import SpeakerGallery

    with tempfile.TemporaryDirectory() as tmp:
        settings = _speakers_tmp_settings(tmp)
        g = SpeakerGallery(settings.speakers_file)
        g.enroll_owner(np.array([1.0, 0.0, 0.0], dtype=np.float32))
        drifted = [0.0, 1.0, 0.0]
        g._speakers["owner"]["embeddings"].append(drifted)
        g._speakers["owner"]["embeddings"].append(drifted)
        g.save()
        with patch("src.main.load_settings", return_value=settings):
            rc = main(["speakers", "audit", "owner"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRIFT" in out
