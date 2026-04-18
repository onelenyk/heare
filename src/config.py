"""Settings and shared enums.

Mutable runtime state (current mode) lives in ~/.heare/mode so it can be
hot-reloaded without restarting the daemon.
"""
from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger("heare.config")


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
    min_action_confidence: float = 0.8
    bot_speaking_cooldown_seconds: float = 2.0
    warmup_interval_seconds: float = 240.0
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
    claude_decider_model: str = "haiku"
    groq_api_key: str | None = None
    groq_language: str = "uk"
    # Speaker recognition (off by default — torch/speechbrain live under
    # [project.optional-dependencies].speaker and are lazy-imported)
    speaker_id_enabled: bool = False
    speaker_id_threshold_match: float = 0.75
    speaker_id_threshold_unknown: float = 0.55
    speaker_id_sticky_threshold: float = 0.80
    speaker_id_sticky_seconds: float = 5.0
    speaker_id_min_duration_ms: int = 400
    speaker_id_accum_target_ms: int = 3000
    speaker_id_centroid_k: int = 5
    speaker_id_ema_alpha: float = 0.1
    speaker_id_auto_enroll_after: int = 3
    speaker_id_auto_enroll_enabled: bool = False
    speaker_command_keyword_required: bool = True
    # Optional shared-secret phrase. When non-empty, saying
    # `<wake-word> <passphrase>` confirms a pending action. Additive —
    # the existing yes/no + speaker-id flow still works. Never logged.
    confirmation_passphrase: str | None = None
    # Wake word / command keyword. Set via config.toml or onboarding. Default is "гава".
    wake_word: str = "гава"
    # Proactivity level for ambient mode: "low" | "medium" | "high"
    # medium = prompt defaults; low = reserved; high = very engaged.
    proactivity_level: str = "medium"
    command_keyword_pattern: str = r"\b(гава|heare|гей)\b"
    speakers_file: Path = field(default_factory=lambda: HEARE_HOME / "speakers.json")
    use_agent_sdk: bool = False
    claude_sdk_cli_path: str | None = None
    # Turn aggregation and conversation memory settings
    # Per plan US-010: default to False for gradual rollout
    turn_aggregation_enabled: bool = False
    focus_mode_turn_timeout: float = 0.5
    ambient_mode_turn_timeout: float = 3.0
    max_turn_duration: float = 30.0
    conversation_memory_enabled: bool = False
    max_conversation_age_hours: float = 24.0
    topic_extraction_enabled: bool = True
    # Phase 1/2.1 — generator pipeline via OpenRouter.
    openrouter_api_key: str | None = None
    openrouter_model: str = "google/gemini-3.1-flash-lite-preview-20260303"
    openrouter_timeout_seconds: float = 5.0
    # Phase 2.1 — action worker.
    action_timeout_seconds: float = 120.0
    intent_queue_max_pending: int = 32

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

    # Build command_keyword_pattern from wake_word if the user customized it
    if settings.wake_word != "гава":
        import re
        escaped = re.escape(settings.wake_word)
        settings.command_keyword_pattern = rf"\b({escaped})\b"

    if settings.confirmation_passphrase is not None:
        if not isinstance(settings.confirmation_passphrase, str):
            logger.warning(
                "confirmation_passphrase must be a string; ignoring (got %s)",
                type(settings.confirmation_passphrase).__name__,
            )
            settings.confirmation_passphrase = None
        else:
            phrase = settings.confirmation_passphrase.strip()
            if phrase and len(phrase) < 5:
                logger.warning(
                    "confirmation_passphrase is very short (len=%d); "
                    "recommend 5+ chars / 2+ words to avoid STT false-positives",
                    len(phrase),
                )

    settings.groq_api_key = os.environ.get("GROQ_API_KEY")
    settings.openrouter_api_key = os.environ.get("OPENROUTER_API_KEY")

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
