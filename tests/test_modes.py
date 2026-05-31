"""Tests for src/agent/modes.py — profile registry, fail-safe, gating."""
from __future__ import annotations

from src.agent.modes import (
    MODE_PROFILES,
    VALID_MODES,
    is_tool_allowed,
    resolve,
)


def test_registry_has_exactly_five_modes() -> None:
    assert set(MODE_PROFILES) == {
        "ambient",
        "focus",
        "silent",
        "assistant",
        "meeting",
    }
    assert set(VALID_MODES) == set(MODE_PROFILES)


# --- characterization: timing + sound pinned to pre-change behavior ---------


def test_turn_timeouts_match_prechange() -> None:
    assert MODE_PROFILES["focus"].turn_timeout == 0.5
    assert MODE_PROFILES["ambient"].turn_timeout == 3.0
    # silent used the ambient (non-FOCUS) timeout path before this change.
    assert MODE_PROFILES["silent"].turn_timeout == 3.0


def test_sound_policy_matches_prechange() -> None:
    # AMBIENT allowed every chime (None == all).
    assert MODE_PROFILES["ambient"].sound_policy is None
    # SILENT allowed none.
    assert MODE_PROFILES["silent"].sound_policy == frozenset()
    # FOCUS allowed only attention/error/input_waiting.
    assert MODE_PROFILES["focus"].sound_policy == frozenset(
        {"attention", "error", "input_waiting"}
    )


# --- fail-safe --------------------------------------------------------------


def test_resolve_known() -> None:
    assert resolve("meeting").name == "meeting"
    assert resolve("FOCUS").name == "focus"  # case-insensitive


def test_resolve_unknown_falls_back_to_ambient() -> None:
    assert resolve("bogus").name == "ambient"
    assert resolve("").name == "ambient"
    assert resolve(None).name == "ambient"


# --- tool gating ------------------------------------------------------------


def test_assistant_denies_nothing() -> None:
    p = MODE_PROFILES["assistant"]
    for t in ("bash", "write", "mcp__macos-use__x", "stop_daemon"):
        assert is_tool_allowed(p, t) is True


def test_meeting_blocks_action_tools() -> None:
    p = MODE_PROFILES["meeting"]
    for t in (
        "bash",
        "write",
        "stop_daemon",
        "restart_daemon",
        "mcp__macos-use__macos-use_click_and_traverse",
        "macos-use_open",
    ):
        assert is_tool_allowed(p, t) is False
    # read-only / passive tools still allowed.
    for t in ("read", "list_skills", "list_capabilities"):
        assert is_tool_allowed(p, t) is True


def test_set_mode_always_allowed_even_in_meeting() -> None:
    assert is_tool_allowed(MODE_PROFILES["meeting"], "set_mode") is True
    assert is_tool_allowed(MODE_PROFILES["silent"], "set_mode") is True
