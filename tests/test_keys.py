"""Keys live in the Keychain; the file is the fallback and the past.

Every test here that wants a Keychain gets a fake one — a dict behind
the two calls this app makes — and says so by setting the backend
explicitly. tests/conftest.py puts everything else on the file backend
so nothing ever touches the developer's real Keychain.
"""

from __future__ import annotations

import logging

import pytest

from src import config
from src import keys


class _FakeKeyring:
    """keyring's surface, minus the Security framework."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.store.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.store[(service, account)] = value

    def get_keyring(self):
        return self


@pytest.fixture
def keychain(monkeypatch):
    fake = _FakeKeyring()
    monkeypatch.setattr(keys, "_keyring", lambda: fake)
    monkeypatch.setenv("HEARE_KEYS_BACKEND", "keychain")
    return fake


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HEARE_HOME", tmp_path)
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    return tmp_path


# ── which store ─────────────────────────────────────────────────────


def test_the_backend_can_be_forced_either_way(monkeypatch) -> None:
    monkeypatch.setenv("HEARE_KEYS_BACKEND", "file")
    assert keys.backend() == "file"
    monkeypatch.setenv("HEARE_KEYS_BACKEND", "keychain")
    assert keys.backend() == "keychain"


def test_no_keyring_backend_means_a_file(monkeypatch) -> None:
    monkeypatch.delenv("HEARE_KEYS_BACKEND", raising=False)

    def _broken():
        raise RuntimeError("no backend")

    monkeypatch.setattr(keys, "_keyring", _broken)
    assert keys.backend() == "file"


# ── reading ─────────────────────────────────────────────────────────


def test_the_environment_wins_over_the_keychain(keychain, monkeypatch) -> None:
    keychain.set_password("heare", "DEEPSEEK_API_KEY", "sk-from-keychain")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-shell")
    assert keys.get("DEEPSEEK_API_KEY") == "sk-from-shell"
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    assert keys.get("DEEPSEEK_API_KEY") == "sk-from-keychain"


def test_blank_is_absent(keychain, monkeypatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    keychain.set_password("heare", "GROQ_API_KEY", "   ")
    assert keys.get("GROQ_API_KEY") is None
    monkeypatch.setenv("GROQ_API_KEY", "")
    assert keys.get("GROQ_API_KEY") is None


def test_settings_read_the_keychain(keychain, home, monkeypatch) -> None:
    """load_settings used to read os.environ and nothing else."""
    for name in keys.key_names():
        monkeypatch.delenv(name, raising=False)
    keychain.set_password("heare", "OPENROUTER_API_KEY", "sk-or-live")
    settings = config.load_settings()
    assert settings.openrouter_api_key == "sk-or-live"
    assert settings.deepseek_api_key is None


# ── storing ─────────────────────────────────────────────────────────


def test_store_puts_it_in_the_keychain_and_this_process(keychain, home, monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert keys.store("DEEPSEEK_API_KEY", " sk-new ") == "keychain"
    assert keychain.get_password("heare", "DEEPSEEK_API_KEY") == "sk-new"
    assert keys.get("DEEPSEEK_API_KEY") == "sk-new"
    assert not (home / ".env").exists(), "a Keychain machine must not grow a .env"


def test_a_keychain_that_does_not_keep_the_value_is_an_error(keychain, monkeypatch) -> None:
    keychain.set_password = lambda *a: None  # writes vanish
    with pytest.raises(RuntimeError, match="did not keep"):
        keys.store("DEEPSEEK_API_KEY", "sk-x")


def test_store_on_the_file_backend_writes_env(home, monkeypatch) -> None:
    monkeypatch.setenv("HEARE_KEYS_BACKEND", "file")
    assert keys.store("GROQ_API_KEY", "gsk_x") == "file"
    assert "GROQ_API_KEY=gsk_x" in (home / ".env").read_text()


# ── where, for the doctor ───────────────────────────────────────────


def test_where_tells_shell_from_file_from_keychain(keychain, home, monkeypatch) -> None:
    (home / ".env").write_text("GROQ_API_KEY=gsk_file\n")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_file")
    assert keys.where("GROQ_API_KEY") == "file"
    monkeypatch.setenv("GROQ_API_KEY", "gsk_shell")
    assert keys.where("GROQ_API_KEY") == "shell"
    monkeypatch.delenv("GROQ_API_KEY")
    assert keys.where("GROQ_API_KEY") is None
    keychain.set_password("heare", "GROQ_API_KEY", "gsk_kc")
    assert keys.where("GROQ_API_KEY") == "keychain"


# ── migration ───────────────────────────────────────────────────────


def test_keys_move_out_of_env_and_other_lines_stay(keychain, home) -> None:
    env = home / ".env"
    env.write_text(
        "# my keys\nGROQ_API_KEY=gsk_a\nUNRELATED=keep-me\n\nDEEPSEEK_API_KEY=sk-b\n"
    )
    moved = keys.migrate_env_file()
    assert sorted(moved) == ["DEEPSEEK_API_KEY", "GROQ_API_KEY"]
    assert keychain.get_password("heare", "GROQ_API_KEY") == "gsk_a"
    assert keychain.get_password("heare", "DEEPSEEK_API_KEY") == "sk-b"
    text = env.read_text()
    assert "# my keys" in text and "UNRELATED=keep-me" in text
    assert "API_KEY" not in text
    assert keys.migrate_env_file() == [], "a second run finds nothing to do"


def test_a_file_that_held_only_keys_is_removed(keychain, home) -> None:
    env = home / ".env"
    env.write_text("GROQ_API_KEY=gsk_a\n\n")
    keys.migrate_env_file()
    assert not env.exists()


def test_a_conflicting_keychain_value_leaves_the_file_alone(keychain, home, caplog) -> None:
    env = home / ".env"
    env.write_text("GROQ_API_KEY=gsk_file\n")
    keychain.set_password("heare", "GROQ_API_KEY", "gsk_other")
    with caplog.at_level(logging.WARNING, logger="heare.keys"):
        assert keys.migrate_env_file() == []
    assert env.read_text() == "GROQ_API_KEY=gsk_file\n"
    assert keychain.get_password("heare", "GROQ_API_KEY") == "gsk_other"
    assert "different value" in caplog.text


def test_migration_happens_inside_load_env(keychain, home, monkeypatch) -> None:
    """Every entry point calls load_env; that is where the file empties."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    (home / ".env").write_text("GROQ_API_KEY=gsk_a\n")
    assert config.load_env() is None, "nothing left to read"
    assert keys.get("GROQ_API_KEY") == "gsk_a"


def test_file_backend_never_migrates(home, monkeypatch) -> None:
    monkeypatch.setenv("HEARE_KEYS_BACKEND", "file")
    (home / ".env").write_text("GROQ_API_KEY=gsk_a\n")
    assert keys.migrate_env_file() == []
    assert (home / ".env").read_text() == "GROQ_API_KEY=gsk_a\n"
