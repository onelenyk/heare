"""Tests for SessionState — composition, mode listener, flush hook."""
from __future__ import annotations

import subprocess

from src.pipeline.language_state import LanguageState
from src.pipeline.session_state import SessionState


def test_composes_untouched_language_state() -> None:
    ls = LanguageState(initial="uk")
    ss = SessionState(ls, initial_mode="ambient")
    assert ss.language_state is ls
    assert ss.language == "uk"
    # Mutating language goes through the original LanguageState only.
    ls.set_language("en")
    assert ss.language == "en"


def test_language_state_file_unmodified() -> None:
    """Composition, not absorption — language_state.py must be untouched."""
    out = subprocess.run(
        ["git", "diff", "--", "src/pipeline/language_state.py"],
        capture_output=True,
        text=True,
        cwd=__import__("pathlib").Path(__file__).resolve().parents[1],
    )
    assert out.stdout.strip() == "", (
        "language_state.py must remain unmodified (compose, don't absorb)"
    )


def test_seeded_from_initial_mode() -> None:
    ss = SessionState(LanguageState(), initial_mode="meeting")
    assert ss.mode == "meeting"
    # Unknown initial mode fails safe to ambient.
    ss2 = SessionState(LanguageState(), initial_mode="bogus")
    assert ss2.mode == "ambient"


def test_set_mode_fires_own_listener_on_change_only() -> None:
    ss = SessionState(LanguageState(), initial_mode="ambient")
    seen: list[str] = []
    ss.set_mode_change_listener(lambda p: seen.append(p.name))

    assert ss.set_mode("focus") is True
    assert ss.mode == "focus"
    assert seen == ["focus"]

    # No-op when unchanged.
    assert ss.set_mode("focus") is False
    assert seen == ["focus"]


def test_mode_listener_is_separate_from_language_listener() -> None:
    ls = LanguageState()
    lang_seen: list[str] = []
    ls.set_change_listener(lambda lang: lang_seen.append(lang))
    ss = SessionState(ls, initial_mode="ambient")
    mode_seen: list[str] = []
    ss.set_mode_change_listener(lambda p: mode_seen.append(p.name))

    ss.set_mode("silent")
    assert mode_seen == ["silent"]
    assert lang_seen == []  # mode change must not touch language listener

    ls.set_language("uk")
    assert lang_seen == ["uk"]
    assert mode_seen == ["silent"]  # language change must not touch mode


def test_flush_hook_invoked_by_flush_pending() -> None:
    ss = SessionState(LanguageState())
    calls: list[int] = []
    ss.register_flush_hook(lambda: calls.append(1))
    ss.flush_pending()
    assert calls == [1]


def test_flush_pending_safe_without_hook() -> None:
    SessionState(LanguageState()).flush_pending()  # must not raise
