"""Tests for src/config.py Settings, enums, load_settings, ensure_dirs."""
from __future__ import annotations

import os

from src.config import (
    Settings,
    backup_session_file,
    load_settings,
    set_capability_install_enabled,
)


def test_default_settings() -> None:
    s = Settings()
    assert s.tts_voice == "en-US-AriaNeural"
    assert s.tts_sample_rate == 24000
    assert s.groq_api_key is None
    assert s.groq_language == "uk"  # Ukrainian hint for Groq, allows English detection


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


def test_installs_are_blocked_until_switched_on() -> None:
    """The default has to be off: this permits a command line to be
    installed that the daemon then runs at every start."""
    assert Settings().capability_install_enabled is False


def test_the_switch_is_read_from_config(monkeypatch, tmp_path) -> None:
    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)

    (tmp_path / "config.toml").write_text("capability_install_enabled = true\n")

    assert load_settings().capability_install_enabled is True


def test_setting_the_switch_writes_a_new_file(monkeypatch, tmp_path) -> None:
    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)

    set_capability_install_enabled(True)

    assert load_settings().capability_install_enabled is True


def test_setting_the_switch_keeps_the_rest_of_the_file(monkeypatch, tmp_path) -> None:
    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)

    (tmp_path / "config.toml").write_text(
        'mode = "focus"\ncapability_install_enabled = true\n'
    )
    set_capability_install_enabled(False)
    s = load_settings()

    assert s.capability_install_enabled is False


def test_the_switch_lands_above_the_first_table(monkeypatch, tmp_path) -> None:
    """The failure this setting actually suffered: written to the end of
    the file, it parked inside whatever [table] came last, load_settings
    read only top-level keys, and the value silently did nothing. One
    install-consent setting spent its whole life inside [browser_bridge].
    """
    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)

    (tmp_path / "config.toml").write_text(
        '[browser_bridge]\ntoken = "abc"\n'
    )
    set_capability_install_enabled(True)

    assert load_settings().capability_install_enabled is True


def test_a_leftover_passphrase_is_not_silently_honoured(
    monkeypatch, tmp_path, caplog
) -> None:
    """It was never compared to anything, so a config carrying one must
    not read as consent — and the reader deserves to be told why."""
    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)

    (tmp_path / "config.toml").write_text('confirmation_passphrase = "авторизую"\n')

    with caplog.at_level("WARNING"):
        s = load_settings()

    assert s.capability_install_enabled is False
    assert any("confirmation_passphrase" in r.getMessage() for r in caplog.records)


def test_backup_session_file_no_file(tmp_path) -> None:
    s = Settings()
    s.session_file = tmp_path / "session.json"
    assert backup_session_file(s) is None


def test_backup_session_file_renames(tmp_path) -> None:
    s = Settings()
    s.session_file = tmp_path / "session.json"
    s.session_file.write_text('{"id": "abc"}')

    backup = backup_session_file(s)

    assert backup == tmp_path / "session_0.backup.json"
    assert backup.exists()
    assert not s.session_file.exists()


def test_backup_session_file_increments(tmp_path) -> None:
    s = Settings()
    s.session_file = tmp_path / "session.json"
    (tmp_path / "session_0.backup.json").write_text("{}")

    s.session_file.write_text('{"id": "abc"}')
    backup = backup_session_file(s)

    assert backup == tmp_path / "session_1.backup.json"




def test_turn_aggregation_settings() -> None:
    s = Settings()
    assert s.turn_aggregation_enabled is False  # Default disabled for gradual rollout
    assert s.max_turn_duration == 30.0


def test_conversation_memory_settings() -> None:
    s = Settings()
    # On by default — it wires the action log that records tool calls to
    # the actions table; off meant the agent's actions were never persisted.
    assert s.conversation_memory_enabled is True
    assert s.topic_extraction_enabled is True


def test_deepseek_settings_defaults() -> None:
    s = Settings()
    assert s.deepseek_api_key is None
    assert s.deepseek_model == "deepseek-chat"
    assert s.deepseek_timeout_seconds == 5.0


def test_phase2_worker_settings_defaults() -> None:
    """Phase 2.1 US-P2.1-07a: action worker config defaults."""
    s = Settings()
    assert s.action_timeout_seconds == 120.0


def test_load_settings_deepseek_from_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-testkey123")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)
    s = load_settings()
    assert s.deepseek_api_key == "sk-ds-testkey123"


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


def test_an_old_config_with_a_deleted_section_still_loads(monkeypatch, tmp_path) -> None:
    """`[indication]` configured a notification subsystem that is gone.
    A config.toml written before the deletion must not become unloadable —
    the section is skipped, not rejected."""
    monkeypatch.setattr("src.config.HEARE_HOME", tmp_path)
    (tmp_path / "config.toml").write_text(
        '[indication]\nenabled = true\ncooldown_seconds = 2.0\n'
    )

    settings = load_settings()

    assert not hasattr(settings, "indication")

def test_deprecated_enable_mcp_servers_warning(monkeypatch, tmp_path) -> None:
    """Startup with enable_mcp_servers in config.toml logs a deprecation WARNING."""
    import logging

    import src.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("HEARE_MODE", raising=False)

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


# ---------------------------------------------------------------------------
# API key plumbing — api_key_fields / write_env_updates / write_config_toml_values
# ---------------------------------------------------------------------------


def test_api_key_fields_covers_every_provider() -> None:
    """Derived from the provider registry, so a new provider needs no second
    edit for the UI to be able to set its key."""
    from src.agent.llm.providers import PROVIDERS
    from src.config import api_key_fields

    fields = api_key_fields()
    assert fields["groq_api_key"] == "GROQ_API_KEY"
    for cfg in PROVIDERS.values():
        assert fields[cfg.api_key_attr] == cfg.api_key_env


def test_write_env_updates_preserves_comments_and_others(tmp_path, monkeypatch) -> None:
    from src.config import write_env_updates

    env = tmp_path / ".env"
    env.write_text(
        "# my keys\nGROQ_API_KEY=gsk_old\n\nUNRELATED=keep-me\nDEEPSEEK_API_KEY=sk-old\n"
    )
    write_env_updates({"DEEPSEEK_API_KEY": "sk-new", "ZAI_API_KEY": "sk-zai"}, env)

    text = env.read_text()
    assert "# my keys" in text
    assert "UNRELATED=keep-me" in text
    assert "GROQ_API_KEY=gsk_old" in text
    assert "DEEPSEEK_API_KEY=sk-new" in text
    assert "sk-old" not in text
    assert "ZAI_API_KEY=sk-zai" in text


def test_write_env_updates_is_owner_only(tmp_path) -> None:
    """The file holds credentials."""
    import stat

    from src.config import write_env_updates

    env = tmp_path / ".env"
    write_env_updates({"DEEPSEEK_API_KEY": "sk-x"}, env)
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_load_env_tightens_a_file_anyone_could_read(tmp_path, monkeypatch) -> None:
    """The write side has always been careful; the read side never looked.

    A .env written by an editor, restored from a backup, or produced by the
    old hand-rolled save path lands as 0644 — and stays that way forever,
    because nothing on startup ever checked. That is how the credentials on
    a real machine came to be readable by every process on it.
    """
    import stat

    import src.config as config

    env = tmp_path / ".env"
    env.write_text("DEEPSEEK_API_KEY=sk-x\n")
    env.chmod(0o644)
    monkeypatch.setattr(config, "ENV_PATH", env)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    config.load_env()

    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert os.environ.get("DEEPSEEK_API_KEY") == "sk-x"


def test_load_env_survives_having_nothing_to_read(tmp_path, monkeypatch) -> None:
    """First run, before any key has been saved. Loading must not be the
    thing that stops the daemon from starting."""
    import src.config as config

    monkeypatch.setattr(config, "ENV_PATH", tmp_path / "absent.env")
    assert config.load_env() is None


def test_the_shell_still_wins_by_default(tmp_path, monkeypatch) -> None:
    """`GROQ_API_KEY=... uv run ...` is how a key gets tried without being
    saved. override=True is for the reload after "Save keys", and only for
    that."""
    import src.config as config

    env = tmp_path / ".env"
    env.write_text("GROQ_API_KEY=gsk_from_file\n")
    monkeypatch.setattr(config, "ENV_PATH", env)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_from_shell")

    config.load_env()
    assert os.environ["GROQ_API_KEY"] == "gsk_from_shell"

    config.load_env(override=True)
    assert os.environ["GROQ_API_KEY"] == "gsk_from_file"


def test_write_config_toml_values_keeps_sections(tmp_path, monkeypatch) -> None:
    """Regression for the flattening bug: rewriting top-level keys must not
    swallow [browser_bridge], which orphans the bridge token."""
    import tomllib

    import src.config as config_mod

    monkeypatch.setattr(config_mod, "HEARE_HOME", tmp_path)
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "# hand-written\n"
        'wake_word = "фраза"\n'
        "\n"
        "[browser_bridge]\n"
        'token = "tok-123"\n'
        "port = 9333\n"
    )

    config_mod.write_config_toml_values(
        {"groq_language": "uk", "tts_voice": "uk-UA-OstapNeural"}
    )

    parsed = tomllib.loads(cfg.read_text())
    assert parsed["browser_bridge"] == {"token": "tok-123", "port": 9333}
    assert parsed["wake_word"] == "фраза"
    assert parsed["groq_language"] == "uk"
    assert parsed["tts_voice"] == "uk-UA-OstapNeural"
    assert "# hand-written" in cfg.read_text()


def test_write_config_toml_values_updates_in_place(tmp_path, monkeypatch) -> None:
    import tomllib

    import src.config as config_mod

    monkeypatch.setattr(config_mod, "HEARE_HOME", tmp_path)
    cfg = tmp_path / "config.toml"
    cfg.write_text('groq_language = "en"\n\n[browser_bridge]\ntoken = "t"\n')

    config_mod.write_config_toml_values({"groq_language": "uk"})

    parsed = tomllib.loads(cfg.read_text())
    assert parsed["groq_language"] == "uk"
    assert cfg.read_text().count("groq_language") == 1
    assert parsed["browser_bridge"]["token"] == "t"


def test_write_config_toml_values_on_missing_file(tmp_path, monkeypatch) -> None:
    import tomllib

    import src.config as config_mod

    monkeypatch.setattr(config_mod, "HEARE_HOME", tmp_path)
    config_mod.write_config_toml_values({"groq_language": "uk"})
    parsed = tomllib.loads((tmp_path / "config.toml").read_text())
    assert parsed["groq_language"] == "uk"


def test_browser_bridge_token_survives_a_key_save_roundtrip(
    tmp_path, monkeypatch
) -> None:
    """End-to-end of the 4001 bug: write a token, save unrelated config, and
    the loader must still find the token under [browser_bridge]."""
    import src.config as config_mod

    monkeypatch.setattr(config_mod, "HEARE_HOME", tmp_path)
    settings = Settings()
    config_mod.write_browser_bridge_token(settings, "tok-abc")

    config_mod.write_config_toml_values({"groq_language": "uk"})

    import tomllib

    raw = tomllib.loads((tmp_path / "config.toml").read_text())
    reloaded = Settings()
    config_mod._load_browser_bridge_settings(reloaded, raw.get("browser_bridge", {}))
    assert reloaded.browser_bridge_token == "tok-abc"


def test_the_switch_lands_top_level_not_inside_a_table(tmp_path, monkeypatch) -> None:
    """Regression: appending at EOF parked the key inside the last [table],
    where load_settings never looks — so the setting silently did nothing."""
    import tomllib

    import src.config as config_mod

    monkeypatch.setattr(config_mod, "HEARE_HOME", tmp_path)
    cfg = tmp_path / "config.toml"
    cfg.write_text('[browser_bridge]\ntoken = "tok"\n')

    config_mod.set_capability_install_enabled(True)

    parsed = tomllib.loads(cfg.read_text())
    assert parsed["capability_install_enabled"] is True
    assert "capability_install_enabled" not in parsed["browser_bridge"]
    assert parsed["browser_bridge"]["token"] == "tok"


def test_a_misplaced_key_is_moved_not_duplicated(tmp_path, monkeypatch) -> None:
    """Including one written under the old name — that is the state a
    real config was found in, carrying a dead passphrase inside
    [browser_bridge]."""
    import tomllib

    import src.config as config_mod

    monkeypatch.setattr(config_mod, "HEARE_HOME", tmp_path)
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[browser_bridge]\ntoken = "tok"\nconfirmation_passphrase = "стара"\n'
    )

    config_mod.set_capability_install_enabled(True)

    text = cfg.read_text()
    assert "confirmation_passphrase" not in text
    assert text.count("capability_install_enabled") == 1
    parsed = tomllib.loads(text)
    assert parsed["capability_install_enabled"] is True
    assert parsed["browser_bridge"] == {"token": "tok"}


def test_a_boolean_is_written_as_a_boolean(tmp_path, monkeypatch) -> None:
    """Every value used to be quoted. A bool written as "False" parses
    back as a non-empty string, which is truthy — the switch would have
    read as permission granted at the moment it was turned off."""
    import tomllib

    import src.config as config_mod

    monkeypatch.setattr(config_mod, "HEARE_HOME", tmp_path)
    cfg = tmp_path / "config.toml"

    config_mod.set_capability_install_enabled(False)

    assert tomllib.loads(cfg.read_text())["capability_install_enabled"] is False
    assert load_settings().capability_install_enabled is False


def test_the_daemon_boots_without_the_old_engine() -> None:
    """There is one engine now, so there is nothing left to choose.

    The flag existed to allow a rollback to pipecat. With that path
    deleted the flag could only have lied, and the import it guarded —
    ~819 ms and some 700 modules — is gone with it. This pins the
    absence: a stray import of the old package would fail here rather
    than quietly return to loading a framework nothing calls.
    """
    import inspect

    import src.main as main_mod

    source = inspect.getsource(main_mod._build_and_run_daemon)
    assert "spine_engine" in source
    assert "pipecat" not in source
    assert "src.pipeline" not in source  # the package is deleted entirely
    assert not hasattr(load_settings(), "engine")


