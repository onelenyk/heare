"""The key store: an explicit source for every key, and adopting one."""

from __future__ import annotations

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
