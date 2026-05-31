"""US-004 — set_mode tool: validation, flush-before-switch, persistence."""
from __future__ import annotations

import tempfile
from pathlib import Path

from src.agent.modes import MODE_PROFILES, is_tool_allowed
from src.agent.tools.direct import _execute_set_mode
from src.config import load_settings
from src.pipeline.language_state import LanguageState
from src.pipeline.session_state import (
    SessionState,
    set_active_session_state,
)


def _settings_with_tmp_mode_file(tmp: str):
    s = load_settings()
    return s


async def test_invalid_mode_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        s = _settings_with_tmp_mode_file(tmp)
        res = await _execute_set_mode("teleport", s)
    assert res["success"] is False
    assert "Invalid mode" in res["error"]
    assert "ambient" in res["error"]  # lists valid modes


async def test_valid_mode_persists_and_flips_live_state() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        s = _settings_with_tmp_mode_file(tmp)
        ss = SessionState(LanguageState(), initial_mode="ambient")
        flushed: list[int] = []
        order: list[str] = []
        ss.register_flush_hook(lambda: (flushed.append(1), order.append("flush")))
        ss.set_mode_change_listener(lambda p: order.append(f"mode:{p.name}"))
        set_active_session_state(ss)
        try:
            res = await _execute_set_mode("meeting", s)
        finally:
            set_active_session_state(None)

        assert res["success"] is True
        assert "meeting" in res["output"]
        # Live state flipped.
        assert ss.mode == "meeting"
        # Flush happened BEFORE the mode flip (no dropped mid-sentence turn).
        assert flushed == [1]
        assert order == ["flush", "mode:meeting"]


async def test_set_mode_works_without_active_session_state() -> None:
    """Persists even if no live pipeline (e.g. CLI / cold path)."""
    with tempfile.TemporaryDirectory() as tmp:
        s = _settings_with_tmp_mode_file(tmp)
        set_active_session_state(None)
        res = await _execute_set_mode("focus", s)
        assert res["success"] is True


def test_set_mode_exempt_in_every_mode() -> None:
    for profile in MODE_PROFILES.values():
        assert is_tool_allowed(profile, "set_mode") is True


def test_set_mode_registered_in_registry_and_schema() -> None:
    from src.agent.tools.definitions import get_tool_names, get_tool
    from src.agent.tools.schemas import _TOOL_SPECS

    assert "set_mode" in get_tool_names()
    assert get_tool("set_mode") is not None
    assert "set_mode" in _TOOL_SPECS
    props, required, _ = _TOOL_SPECS["set_mode"]
    assert required == ["mode"]
    assert set(props["mode"]["enum"]) == {
        "ambient",
        "focus",
        "silent",
        "assistant",
        "meeting",
    }
