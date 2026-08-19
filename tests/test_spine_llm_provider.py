"""Choosing a provider used to be theatre.

The dashboard has had a provider switch and a model dropdown since long
before this engine existed. Both wrote to State; the engine read neither.
The base URL and the model were two constants at the top of
``src/spine/llm.py``, so every reply came from ``deepseek-chat`` no matter
what the UI said — and the only way to find out was to notice the answers
still sounded the same.

These are the assertions that make the switch real.
"""

from __future__ import annotations

import pytest

from src.spine.llm import resolve_llm


class _Settings:
    """Only the attributes resolve_llm reads. A real Settings has ~200."""

    def __init__(self, **kw) -> None:
        self.llm_provider = "deepseek"
        self.deepseek_api_key = None
        self.zai_api_key = None
        self.opencode_api_key = None
        for name, value in kw.items():
            setattr(self, name, value)


def test_the_chosen_provider_is_the_one_used() -> None:
    cfg = resolve_llm(
        _Settings(llm_provider="opencode", opencode_api_key="sk-oc", deepseek_api_key="sk-ds")
    )
    assert cfg.provider == "opencode"
    assert cfg.base_url == "https://opencode.ai/zen/go/v1"
    assert cfg.model == "minimax-m2.7"


def test_a_chosen_model_beats_the_registry_default() -> None:
    """The dropdown offers deepseek-reasoner; it has to arrive somewhere."""
    cfg = resolve_llm(
        _Settings(deepseek_api_key="sk-ds", deepseek_model="deepseek-reasoner")
    )
    assert cfg.model == "deepseek-reasoner"


def test_the_api_style_travels_with_the_choice() -> None:
    """Z.AI speaks Anthropic, not OpenAI. Resolving must say so, or the
    request goes out in the wrong shape entirely."""
    cfg = resolve_llm(_Settings(llm_provider="zai", zai_api_key="sk-zai"))
    assert cfg.api_style == "anthropic"
    assert resolve_llm(_Settings(deepseek_api_key="sk-ds")).api_style == "openai"


def test_choosing_a_provider_you_have_no_key_for_does_not_go_mute() -> None:
    """A dropdown must not be able to end the conversation. The dashboard
    already shows which providers are configured; falling back to one that
    answers is better than an exception on the voice path."""
    cfg = resolve_llm(_Settings(llm_provider="zai", deepseek_api_key="sk-ds"))
    assert cfg.provider == "deepseek"


def test_with_no_key_anywhere_it_says_which_ones_it_wanted() -> None:
    with pytest.raises(RuntimeError) as excinfo:
        resolve_llm(_Settings(llm_provider="zai"))
    message = str(excinfo.value)
    assert "DEEPSEEK_API_KEY" in message and "ZAI_API_KEY" in message


def test_an_unknown_name_is_not_fatal() -> None:
    """`~/.heare/provider` is a plain text file a person can edit."""
    assert resolve_llm(_Settings(llm_provider="gpt9", deepseek_api_key="sk-ds")).provider == "deepseek"


def test_a_switch_is_heard_on_the_next_turn_not_the_next_restart() -> None:
    """Resolved once at build time, a change would land on restart — which,
    for something meant to run all day, means never."""
    from src.spine.main import _live_cfg

    settings = _Settings(deepseek_api_key="sk-ds", opencode_api_key="sk-oc")
    live = _live_cfg(settings, resolve_llm(settings))
    assert live().provider == "deepseek"

    settings.llm_provider = "opencode"
    assert live().provider == "opencode"


def test_losing_every_key_mid_conversation_keeps_the_last_good_one() -> None:
    from src.spine.main import _live_cfg

    settings = _Settings(deepseek_api_key="sk-ds")
    live = _live_cfg(settings, resolve_llm(settings))
    settings.deepseek_api_key = None
    assert live().provider == "deepseek"
