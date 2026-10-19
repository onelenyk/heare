"""The key store: an explicit source for every key, and adopting one."""

from __future__ import annotations

import json
import stat

import pytest

from heare import keys


def test_a_key_in_the_environment_has_the_environment_as_its_source(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-value")
    assert keys.source("DEEPSEEK_API_KEY") == "env"
    assert keys.get("DEEPSEEK_API_KEY") == "test-value"


def test_a_stored_key_has_the_store_as_its_source():
    keys.store("GROQ_API_KEY", "test-value")
    assert keys.source("GROQ_API_KEY") == "stored"
    assert keys.get("GROQ_API_KEY") == "test-value"


def test_a_missing_key_has_no_source():
    assert keys.source("ZAI_API_KEY") is None
    assert keys.get("ZAI_API_KEY") is None


def test_an_empty_environment_variable_is_not_a_source(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "   ")
    keys.store("DEEPSEEK_API_KEY", "test-value")
    assert keys.source("DEEPSEEK_API_KEY") == "stored"


def test_the_environment_wins_over_the_store(monkeypatch):
    keys.store("DEEPSEEK_API_KEY", "stored-value")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-value")
    assert keys.source("DEEPSEEK_API_KEY") == "env"
    assert keys.get("DEEPSEEK_API_KEY") == "env-value"


def test_sources_names_every_key():
    assert keys.sources() == {name: None for name in keys.NAMES}
    assert set(keys.NAMES) == {
        "DEEPSEEK_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY", "OPENCODE_API_KEY", "ZAI_API_KEY",
    }


def test_adopt_copies_the_environment_value_into_the_store(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-value")
    assert keys.adopt("DEEPSEEK_API_KEY") is True
    assert keys.view()["DEEPSEEK_API_KEY"] == {
        "source": "env", "env": True, "stored": True,
    }
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    assert keys.source("DEEPSEEK_API_KEY") == "stored"
    assert keys.get("DEEPSEEK_API_KEY") == "env-value"


def test_adopt_with_nothing_in_the_environment_is_false():
    assert keys.adopt("DEEPSEEK_API_KEY") is False
    assert keys.source("DEEPSEEK_API_KEY") is None


# -- a server: no keyring, so a file -----------------------------------------


class _Ring:
    """A keyring that answers, or one whose daemon is locked or absent."""

    def __init__(self, works: bool = True) -> None:
        self.works = works
        self.kept: dict[str, str] = {}

    def set_password(self, service: str, name: str, value: str) -> None:
        if not self.works:
            raise RuntimeError("no secret service")
        self.kept[name] = value

    def get_password(self, service: str, name: str) -> str | None:
        if not self.works:
            raise RuntimeError("no secret service")
        return self.kept.get(name)


@pytest.fixture
def on_disk(tmp_path, monkeypatch):
    """The real stores, with the file in a temporary home and no keyring."""
    monkeypatch.delenv("HEARE_KEYS_BACKEND")
    path = tmp_path / ".heare" / "keys.json"
    monkeypatch.setattr(keys, "keys_file", lambda: path)
    monkeypatch.setattr(keys, "_keyring", lambda: None)
    return path


def test_with_no_keyring_a_key_is_kept_in_a_file_only_its_owner_reads(on_disk):
    keys.store("DEEPSEEK_API_KEY", " test-value ")
    assert stat.S_IMODE(on_disk.stat().st_mode) == 0o600
    assert json.loads(on_disk.read_text()) == {"DEEPSEEK_API_KEY": "test-value"}
    assert keys.get("DEEPSEEK_API_KEY") == "test-value"
    assert keys.source("DEEPSEEK_API_KEY") == "stored"


def test_the_file_keeps_every_key_it_was_given(on_disk):
    keys.store("DEEPSEEK_API_KEY", "one")
    keys.store("GROQ_API_KEY", "two")
    keys.store("DEEPSEEK_API_KEY", "three")
    assert json.loads(on_disk.read_text()) == {"DEEPSEEK_API_KEY": "three",
                                               "GROQ_API_KEY": "two"}


def test_the_environment_wins_over_the_file(on_disk, monkeypatch):
    keys.store("GROQ_API_KEY", "stored-value")
    monkeypatch.setenv("GROQ_API_KEY", "env-value")
    assert keys.get("GROQ_API_KEY") == "env-value"
    assert keys.source("GROQ_API_KEY") == "env"


def test_a_broken_file_is_no_key(on_disk):
    on_disk.parent.mkdir(parents=True)
    on_disk.write_text("not json")
    assert keys.get("GROQ_API_KEY") is None


def test_a_keyring_that_answers_is_used_and_the_file_is_not_written(on_disk, monkeypatch):
    ring = _Ring()
    monkeypatch.setattr(keys, "_keyring", lambda: ring)
    keys.store("GROQ_API_KEY", "test-value")
    assert ring.kept == {"GROQ_API_KEY": "test-value"}
    assert not on_disk.exists()
    assert keys.get("GROQ_API_KEY") == "test-value"


def test_a_keyring_that_will_not_answer_hands_the_key_to_the_file(on_disk, monkeypatch):
    monkeypatch.setattr(keys, "_keyring", lambda: _Ring(works=False))
    keys.store("GROQ_API_KEY", "test-value")
    assert json.loads(on_disk.read_text()) == {"GROQ_API_KEY": "test-value"}
    assert keys.get("GROQ_API_KEY") == "test-value"


def test_the_test_switch_never_touches_the_disk(tmp_path, monkeypatch):
    path = tmp_path / "keys.json"
    monkeypatch.setattr(keys, "keys_file", lambda: path)
    keys.store("GROQ_API_KEY", "test-value")
    assert keys.get("GROQ_API_KEY") == "test-value"
    assert not path.exists()


def test_a_backend_that_always_fails_is_no_keyring(monkeypatch):
    import keyring
    from keyring.backends import fail

    monkeypatch.setattr(keyring, "get_keyring", lambda: fail.Keyring())
    assert keys._keyring() is None
