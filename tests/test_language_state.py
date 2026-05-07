"""Tests for LanguageState (Phase 2 PH2-04)."""
from __future__ import annotations

from src.pipeline.language_state import LanguageState


def test_initial_default_is_english() -> None:
    state = LanguageState()
    assert state.language == "en"


def test_initial_override() -> None:
    state = LanguageState(initial="uk")
    assert state.language == "uk"


def test_set_language_changes_value() -> None:
    state = LanguageState()
    assert state.set_language("uk") is True
    assert state.language == "uk"


def test_set_language_returns_false_when_unchanged() -> None:
    state = LanguageState(initial="uk")
    assert state.set_language("uk") is False
    assert state.language == "uk"


def test_set_language_ignores_empty_string() -> None:
    state = LanguageState(initial="en")
    assert state.set_language("") is False
    assert state.language == "en"


def test_listener_fires_on_change() -> None:
    state = LanguageState(initial="en")
    seen: list[str] = []
    state.set_change_listener(seen.append)
    state.set_language("uk")
    assert seen == ["uk"]


def test_listener_does_not_fire_on_no_change() -> None:
    state = LanguageState(initial="uk")
    seen: list[str] = []
    state.set_change_listener(seen.append)
    state.set_language("uk")
    assert seen == []


def test_listener_clear_via_none() -> None:
    state = LanguageState(initial="en")
    seen: list[str] = []
    state.set_change_listener(seen.append)
    state.set_change_listener(None)
    state.set_language("uk")
    assert seen == []


def test_listener_exception_does_not_break_state() -> None:
    state = LanguageState(initial="en")

    def boom(_lang: str) -> None:
        raise RuntimeError("listener exploded")

    state.set_change_listener(boom)
    # Should not propagate; state should still update.
    assert state.set_language("uk") is True
    assert state.language == "uk"
