"""Tests for src/config.py Settings, enums, load_settings, ensure_dirs."""
from __future__ import annotations



from src.config import DeciderState, Mode, Settings, load_settings


def test_default_settings() -> None:
    s = Settings()
    assert s.mode == Mode.AMBIENT
    assert s.tts_voice == "uk-UA-PolinaNeural"
    assert s.tts_sample_rate == 24000
    assert s.heartbeat_interval_minutes == 30
    assert s.confirmation_timeout_seconds == 30
    assert s.transcript_retention_days == 30
    assert s.claude_cli == "claude"
    assert s.groq_api_key is None
    assert s.groq_language == "uk"


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


def test_load_settings_heartbeat_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HEARE_HEARTBEAT_MIN", "5")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("HEARE_MODE", raising=False)
    monkeypatch.delenv("HEARE_CLAUDE_CLI", raising=False)
    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)
    s = load_settings()
    assert s.heartbeat_interval_minutes == 5


def test_load_settings_heartbeat_invalid(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HEARE_HEARTBEAT_MIN", "abc")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("HEARE_MODE", raising=False)
    monkeypatch.delenv("HEARE_CLAUDE_CLI", raising=False)
    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)
    s = load_settings()
    assert s.heartbeat_interval_minutes == 30


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
    assert s.speaker_id_enabled is False
    assert s.speaker_id_threshold_match == 0.75
    assert s.speaker_id_threshold_unknown == 0.55
    assert s.speaker_id_sticky_threshold == 0.80
    assert s.speaker_id_sticky_seconds == 5.0
    assert s.speaker_id_min_duration_ms == 400
    assert s.speaker_id_accum_target_ms == 3000
    assert s.speaker_id_centroid_k == 5
    assert s.speaker_id_ema_alpha == 0.1
    assert s.speaker_id_auto_enroll_after == 3
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
    assert s.generator_mode is False
    assert s.openrouter_api_key is None
    assert s.openrouter_model == "google/gemini-3.1-flash-lite-preview-20260303"
    assert s.openrouter_timeout_seconds == 5.0


def test_load_settings_openrouter_from_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-testkey123")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)
    s = load_settings()
    assert s.openrouter_api_key == "sk-or-testkey123"


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
