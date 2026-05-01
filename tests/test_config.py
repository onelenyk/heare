"""Tests for src/config.py Settings, enums, load_settings, ensure_dirs."""
from __future__ import annotations

from src.config import DeciderState, Mode, Settings, load_settings


def test_default_settings() -> None:
    s = Settings()
    assert s.mode == Mode.AMBIENT
    assert s.tts_voice == "en-US-AriaNeural"
    assert s.tts_sample_rate == 24000
    assert s.confirmation_timeout_seconds == 30
    assert s.transcript_retention_days == 30
    assert s.claude_cli == "claude"
    assert s.groq_api_key is None
    assert s.groq_language == "uk"  # Ukrainian hint for Groq, allows English detection


def test_min_action_confidence_default() -> None:
    assert Settings().min_action_confidence == 0.8


def test_load_settings_groq_from_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk_testkey123")
    monkeypatch.setenv("HEARE_HOME", str(tmp_path))
    # Patch the module-level HEARE_HOME so load_settings resolves paths correctly
    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)
    s = load_settings()
    assert s.groq_api_key == "gsk_testkey123"


def test_load_settings_mode_from_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HEARE_MODE", "silent")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)
    s = load_settings()
    assert s.mode == Mode.SILENT


def test_load_settings_claude_cli_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HEARE_CLAUDE_CLI", "/usr/bin/claude")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("HEARE_MODE", raising=False)
    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)
    s = load_settings()
    assert s.claude_cli == "/usr/bin/claude"


def test_mode_enum_values() -> None:
    assert Mode.SILENT.value == "silent"
    assert Mode.FOCUS.value == "focus"
    assert Mode.AMBIENT.value == "ambient"


def test_decider_state_enum_values() -> None:
    assert DeciderState.LISTENING.value == "listening"
    assert DeciderState.AWAITING_CONFIRMATION.value == "awaiting_confirmation"
    assert DeciderState.EXECUTING.value == "executing"


def test_speaker_id_defaults() -> None:
    s = Settings()
    assert s.speaker_id_enabled is True
    assert s.speaker_id_threshold_match == 0.50
    assert s.speaker_id_threshold_unknown == 0.55
    assert s.speaker_id_sticky_threshold == 0.80
    assert s.speaker_id_sticky_seconds == 5.0
    assert s.speaker_id_min_duration_ms == 400
    assert s.speaker_id_accum_target_ms == 3000
    assert s.speaker_id_centroid_k == 5
    assert s.speaker_id_ema_alpha == 0.1
    assert s.speaker_id_auto_enroll_after == 2
    assert s.speaker_id_auto_enroll_enabled is True
    assert s.speaker_id_auto_enroll_owner_enabled is True
    assert s.speaker_id_auto_enroll_owner_after == 5
    assert s.speakers_file.name == "speakers.json"


def test_wake_word_default() -> None:
    s = Settings()
    assert s.wake_word == "гава"


def test_wake_word_custom(monkeypatch, tmp_path) -> None:
    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)

    config_file = tmp_path / "config.toml"
    config_file.write_text('wake_word = "Хара"\n')
    s = load_settings()
    assert s.wake_word == "Хара"


def test_confirmation_passphrase_default() -> None:
    s = Settings()
    assert s.confirmation_passphrase is None


def test_confirmation_passphrase_custom(monkeypatch, tmp_path) -> None:
    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)

    config_file = tmp_path / "config.toml"
    config_file.write_text('confirmation_passphrase = "авторизую"\n')
    s = load_settings()
    assert s.confirmation_passphrase == "авторизую"


def test_proactivity_level_default() -> None:
    s = Settings()
    assert s.proactivity_level == "medium"


def test_proactivity_level_custom(monkeypatch, tmp_path) -> None:
    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)

    config_file = tmp_path / "config.toml"
    config_file.write_text('proactivity_level = "high"\n')
    s = load_settings()
    assert s.proactivity_level == "high"


def test_turn_aggregation_settings() -> None:
    s = Settings()
    assert s.turn_aggregation_enabled is False  # Default disabled for gradual rollout
    assert s.focus_mode_turn_timeout == 0.5
    assert s.ambient_mode_turn_timeout == 3.0
    assert s.max_turn_duration == 30.0


def test_conversation_memory_settings() -> None:
    s = Settings()
    assert s.conversation_memory_enabled is False  # Default disabled for gradual rollout
    assert s.max_conversation_age_hours == 24.0
    assert s.topic_extraction_enabled is True


def test_openrouter_settings_defaults() -> None:
    s = Settings()
    assert s.openrouter_api_key is None
    assert s.openrouter_model == "google/gemini-3.1-flash-lite-preview-20260303"
    assert s.openrouter_timeout_seconds == 5.0


def test_topic_extraction_backend_default() -> None:
    s = Settings()
    assert s.topic_extraction_backend == "openrouter"


def test_topic_extraction_openrouter_defaults() -> None:
    s = Settings()
    assert s.topic_extraction_openrouter_model == "google/gemini-3.1-flash-lite-preview-20260303"
    assert s.topic_extraction_openrouter_timeout_seconds == 5.0


def test_phase2_worker_settings_defaults() -> None:
    """Phase 2.1 US-P2.1-07a: action worker config defaults."""
    s = Settings()
    assert s.action_timeout_seconds == 120.0
    assert s.intent_queue_max_pending == 32


def test_load_settings_openrouter_from_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-testkey123")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)
    s = load_settings()
    assert s.openrouter_api_key == "sk-or-testkey123"


def test_speaker_namer_defaults() -> None:
    s = Settings()
    assert s.speaker_namer_enabled is True
    assert s.speaker_namer_model == "anthropic/claude-haiku-4.5"
    assert s.speaker_namer_min_turns == 3
    assert s.speaker_namer_debounce_seconds == 10.0
    assert s.speaker_namer_confidence_threshold == 0.8
    assert s.speaker_namer_confidence_hysteresis == 0.05
    assert s.speaker_namer_buffer_size == 10
    assert s.speaker_namer_queue_max == 64
    assert s.speaker_namer_timeout_seconds == 15.0


def test_speaker_namer_toml_roundtrip(monkeypatch, tmp_path) -> None:
    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("HEARE_MODE", raising=False)

    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "speaker_namer_enabled = true\n"
        'speaker_namer_model = "anthropic/claude-sonnet-4.6"\n'
        "speaker_namer_min_turns = 5\n"
        "speaker_namer_debounce_seconds = 20.0\n"
        "speaker_namer_confidence_threshold = 0.9\n"
        "speaker_namer_confidence_hysteresis = 0.1\n"
        "speaker_namer_buffer_size = 20\n"
        "speaker_namer_queue_max = 128\n"
        "speaker_namer_timeout_seconds = 30.0\n"
    )
    s = cfg_mod.load_settings()
    assert s.speaker_namer_enabled is True
    assert s.speaker_namer_model == "anthropic/claude-sonnet-4.6"
    assert s.speaker_namer_min_turns == 5
    assert s.speaker_namer_debounce_seconds == 20.0
    assert s.speaker_namer_confidence_threshold == 0.9
    assert s.speaker_namer_confidence_hysteresis == 0.1
    assert s.speaker_namer_buffer_size == 20
    assert s.speaker_namer_queue_max == 128
    assert s.speaker_namer_timeout_seconds == 30.0


def test_ensure_dirs_creates_directories(tmp_path) -> None:
    s = Settings()
    s.workspace_dir = tmp_path / "workspace"
    s.log_dir = tmp_path / "logs"
    # Override HEARE_HOME side-effect: ensure_dirs also creates HEARE_HOME
    # We override it via monkeypatching the module constant
    import src.config as cfg_mod
    original = cfg_mod.HEARE_HOME
    cfg_mod.HEARE_HOME = tmp_path / "heare_home"
    try:
        s.ensure_dirs()
        assert s.workspace_dir.exists()
        assert s.log_dir.exists()
        assert cfg_mod.HEARE_HOME.exists()
    finally:
        cfg_mod.HEARE_HOME = original


# ---------------------------------------------------------------------------
# IndicationSettings (US-IND-A2)
# ---------------------------------------------------------------------------


def test_indication_defaults_when_section_absent(monkeypatch, tmp_path) -> None:
    """Missing [indication] section yields fully-defaulted IndicationSettings."""
    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("HEARE_MODE", raising=False)

    s = cfg_mod.load_settings()
    assert s.indication.enabled is True
    assert s.indication.sound_enabled is True
    assert s.indication.visual_enabled is True
    assert s.indication.notification_center_enabled is True
    assert s.indication.cooldown_seconds == 1.5
    assert s.indication.quiet_hours == [("22:00", "07:00")]
    # Per-kind defaults match plan §3
    assert s.indication.kinds["attention"].sound is True
    assert s.indication.kinds["info"].sound is False
    assert s.indication.kinds["info"].visual is True
    assert s.indication.kinds["info"].notification is False
    assert s.indication.kinds["input_waiting"].notification is True


def test_indication_partial_override(monkeypatch, tmp_path) -> None:
    """Partial section overrides specified fields, keeps defaults for the rest."""
    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("HEARE_MODE", raising=False)

    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[indication]\n"
        "enabled = false\n"
        "cooldown_seconds = 3.0\n"
        "\n"
        "[indication.kinds.info]\n"
        "sound = true\n"
    )
    s = cfg_mod.load_settings()
    assert s.indication.enabled is False
    assert s.indication.cooldown_seconds == 3.0
    # Untouched fields keep defaults
    assert s.indication.sound_enabled is True
    assert s.indication.kinds["attention"].sound is True
    # Overridden per-kind
    assert s.indication.kinds["info"].sound is True
    # Other per-kind keep defaults
    assert s.indication.kinds["info"].visual is True


def test_indication_invalid_quiet_hours_dropped(monkeypatch, tmp_path, caplog) -> None:
    """Invalid quiet_hours strings log a warning and are dropped."""
    import logging

    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("HEARE_MODE", raising=False)

    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[indication]\n"
        'quiet_hours = ["25:00-07:00", "22:00-07:00", "garbage"]\n'
    )
    records: list[logging.LogRecord] = []

    class _H(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _H(level=logging.WARNING)
    cfg_logger = logging.getLogger("heare.config")
    cfg_logger.addHandler(handler)
    cfg_logger.setLevel(logging.WARNING)
    try:
        s = cfg_mod.load_settings()
    finally:
        cfg_logger.removeHandler(handler)

    # Only the valid entry survives
    assert s.indication.quiet_hours == [("22:00", "07:00")]
    msgs = [r.getMessage() for r in records]
    assert any("25:00-07:00" in m for m in msgs)
    assert any("garbage" in m for m in msgs)


def test_indication_invalid_quiet_hours_type_keeps_default(monkeypatch, tmp_path) -> None:
    """quiet_hours that isn't a list logs and keeps defaults."""
    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("HEARE_MODE", raising=False)

    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[indication]\n"
        'quiet_hours = "22:00-07:00"\n'  # string, not list
    )
    s = cfg_mod.load_settings()
    assert s.indication.quiet_hours == [("22:00", "07:00")]


def test_indication_invalid_cooldown_keeps_default(monkeypatch, tmp_path) -> None:
    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("HEARE_MODE", raising=False)

    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[indication]\n"
        'cooldown_seconds = "abc"\n'
    )
    s = cfg_mod.load_settings()
    assert s.indication.cooldown_seconds == 1.5


def test_deprecated_enable_mcp_servers_warning(monkeypatch, tmp_path) -> None:
    """Startup with enable_mcp_servers in config.toml logs a deprecation WARNING."""
    import logging

    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("HEARE_MODE", raising=False)
    monkeypatch.delenv("HEARE_CLAUDE_CLI", raising=False)

    config_file = tmp_path / "config.toml"
    config_file.write_text('enable_mcp_servers = ["github", "notion"]\n')

    import logging as _logging
    records: list[logging.LogRecord] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Handler()
    handler.setLevel(logging.WARNING)
    cfg_logger = _logging.getLogger("heare.config")
    cfg_logger.addHandler(handler)
    try:
        s = cfg_mod.load_settings()
    finally:
        cfg_logger.removeHandler(handler)

    warning_messages = [r.getMessage() for r in records if r.levelno == logging.WARNING]
    assert any("enable_mcp_servers" in msg for msg in warning_messages), (
        f"expected deprecation warning for enable_mcp_servers, got: {warning_messages}"
    )
    # Field still exists on Settings (retained for one release)
    assert hasattr(s, "enable_mcp_servers")


# ============================================================================
# CCS-01: Settings invariant — refinement_recency must be <= idle window
# ============================================================================


def test_settings_defaults_pass_post_init() -> None:
    """Default Settings() must satisfy the CCS-01 invariant."""
    import pytest as _pytest  # noqa: F401  (kept for parity with other tests)

    s = Settings()
    # Both fields exist and the invariant is respected by defaults.
    assert s.conversation_idle_seconds == 1800.0
    assert s.refinement_recency_seconds == 600.0


def test_settings_refinement_window_exceeding_idle_raises() -> None:
    """Settings(refinement_recency_seconds > conversation_idle_seconds) must trip."""
    import pytest as _pytest

    with _pytest.raises(AssertionError):
        Settings(
            refinement_recency_seconds=2000.0,
            conversation_idle_seconds=1800.0,
        )


def test_settings_refinement_window_equal_idle_ok() -> None:
    """Equal values are explicitly allowed by the <= invariant."""
    s = Settings(
        refinement_recency_seconds=1800.0,
        conversation_idle_seconds=1800.0,
    )
    assert s.refinement_recency_seconds == 1800.0
