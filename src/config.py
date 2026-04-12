"""Settings and shared enums.

Mutable runtime state (current mode) lives in ~/.heare/mode so it can be
hot-reloaded without restarting the daemon.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Mode(str, Enum):
    SILENT = "silent"
    FOCUS = "focus"
    AMBIENT = "ambient"


class DeciderState(str, Enum):
    LISTENING = "listening"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    EXECUTING = "executing"


HEARE_HOME = Path.home() / ".heare"


@dataclass
class Settings:
    mode: Mode = Mode.AMBIENT
    tts_voice: str = "uk-UA-PolinaNeural"
    tts_sample_rate: int = 24000
    heartbeat_interval_minutes: int = 30
    confirmation_timeout_seconds: int = 30
    transcript_retention_days: int = 30
    workspace_dir: Path = field(default_factory=lambda: HEARE_HOME / "workspace")
    session_file: Path = field(default_factory=lambda: HEARE_HOME / "session.json")
    identity_file: Path = field(default_factory=lambda: HEARE_HOME / "identity.json")
    db_path: Path = field(default_factory=lambda: HEARE_HOME / "heare.db")
    log_dir: Path = field(default_factory=lambda: HEARE_HOME / "logs")
    mode_file: Path = field(default_factory=lambda: HEARE_HOME / "mode")
    pid_file: Path = field(default_factory=lambda: HEARE_HOME / "heare.pid")
    claude_cli: str = "claude"
    claude_timeout_seconds: int = 60
    claude_max_retries: int = 3
    claude_max_calls_per_minute: int = 30
    groq_api_key: str | None = None
    groq_language: str = "uk"

    def ensure_dirs(self) -> None:
        for p in (self.workspace_dir, self.log_dir, HEARE_HOME):
            p.mkdir(parents=True, exist_ok=True)


def _read_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def load_settings() -> Settings:
    settings = Settings()
    toml_data = _read_toml(HEARE_HOME / "config.toml")

    for key, value in toml_data.items():
        if hasattr(settings, key):
            current = getattr(settings, key)
            if isinstance(current, Path):
                value = Path(value).expanduser()
            elif isinstance(current, Mode):
                value = Mode(value)
            setattr(settings, key, value)

    settings.groq_api_key = os.environ.get("GROQ_API_KEY")

    mode_override = os.environ.get("HEARE_MODE")
    if mode_override:
        settings.mode = Mode(mode_override)
    elif settings.mode_file.exists():
        raw = settings.mode_file.read_text().strip()
        if raw:
            settings.mode = Mode(raw)

    claude_override = os.environ.get("HEARE_CLAUDE_CLI")
    if claude_override:
        settings.claude_cli = claude_override

    heartbeat_override = os.environ.get("HEARE_HEARTBEAT_MIN")
    if heartbeat_override:
        try:
            settings.heartbeat_interval_minutes = max(1, int(heartbeat_override))
        except ValueError:
            pass

    return settings
