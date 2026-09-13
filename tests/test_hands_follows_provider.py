"""The worker answers from the provider the voice does.

Until 13 September `Hands._provider` took the first provider with a key,
in registry order, and never read `llm_provider`. With DeepSeek and
OpenRouter both keyed and OpenRouter chosen, the voice moved and the
worker stayed — one assistant, two models, and nothing said so.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.agent.hands import Hands


def _settings(**overrides):
    settings = SimpleNamespace(
        deepseek_api_key="sk-deepseek",
        openrouter_api_key="sk-openrouter",
        zai_api_key=None,
        opencode_api_key=None,
        llm_provider="openrouter",
        openrouter_model="deepseek/deepseek-v4-flash",
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def test_hands_follow_the_chosen_provider() -> None:
    provider, base_url, api_key, model = Hands(_settings())._provider()
    assert provider == "openrouter"
    assert api_key == "sk-openrouter"
    assert model == "deepseek/deepseek-v4-flash"
    assert "openrouter" in base_url


def test_hands_follow_a_choice_made_after_construction() -> None:
    """The dashboard mutates Settings in place; the worker must see it on
    its next request, not on the next restart."""
    settings = _settings()
    worker = Hands(settings)
    assert worker._provider()[0] == "openrouter"
    settings.llm_provider = "deepseek"
    assert worker._provider()[0] == "deepseek"


def test_hands_fall_back_like_the_voice_does() -> None:
    """Chosen provider without a key: the same fallback resolve_llm makes,
    so voice and worker still agree."""
    settings = _settings(openrouter_api_key=None)
    assert Hands(settings)._provider()[0] == "deepseek"


def test_hands_with_no_key_anywhere_say_so() -> None:
    settings = _settings(deepseek_api_key=None, openrouter_api_key=None)
    with pytest.raises(RuntimeError, match="no LLM provider"):
        Hands(settings)._provider()
