"""BCDE-005: daemon.log rotates at 10MB via RotatingFileHandler."""
from __future__ import annotations

import logging
import logging.handlers
import tempfile
from pathlib import Path

from src.main import _setup_logging, DAEMON_LOG_MAX_BYTES, DAEMON_LOG_BACKUPS


def test_setup_logging_installs_rotating_handler() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        log_dir = Path(tmp) / "logs"
        handler = _setup_logging(log_dir)
        assert isinstance(handler, logging.handlers.RotatingFileHandler)
        assert handler.maxBytes == DAEMON_LOG_MAX_BYTES
        assert handler.backupCount >= DAEMON_LOG_BACKUPS
        assert (log_dir / "daemon.log").exists()


def test_log_rotation_triggers_backup_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        log_dir = Path(tmp) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        target = log_dir / "daemon.log"
        handler = logging.handlers.RotatingFileHandler(
            target, maxBytes=1024, backupCount=2
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        test_logger = logging.getLogger("heare.rotation_test")
        test_logger.handlers.clear()
        test_logger.propagate = False
        test_logger.setLevel(logging.INFO)
        test_logger.addHandler(handler)
        try:
            for _ in range(200):
                test_logger.info("x" * 100)
        finally:
            handler.close()
            test_logger.removeHandler(handler)
        backup = log_dir / "daemon.log.1"
        assert backup.exists()
