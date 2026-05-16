"""US-003 — execution-time tool-deny gate at both handler chokepoints."""
from __future__ import annotations

import types
from typing import Any

from src.agent.mcp_bridge import McpBridge
from src.agent.modes import MODE_PROFILES
from src.pipeline.language_state import LanguageState
from src.pipeline.session_state import SessionState


class _CapParams:
    def __init__(self, arguments: dict | None = None):
        self.arguments = arguments or {}
        self.result: Any = None

    async def result_callback(self, result: Any) -> None:
        self.result = result


class _FakeSession:
    def __init__(self) -> None:
        self.called = False

    async def call_tool(self, name: str, arguments: dict) -> Any:
        self.called = True
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(text="ran")],
            isError=False,
            structuredContent=None,
        )


# --- MCP chokepoint ---------------------------------------------------------


async def test_mcp_handler_blocks_denied_tool_in_meeting() -> None:
    ss = SessionState(LanguageState(), initial_mode="meeting")
    sess = _FakeSession()
    handler = McpBridge._make_handler(
        "mcp__macos-use__macos-use_click_and_traverse",
        sess,
        None,
        ss,
    )
    params = _CapParams({"x": 1})
    await handler(params)

    assert sess.called is False  # short-circuited, server never called
    assert params.result["success"] is False
    assert "meeting" in params.result["error"]


async def test_mcp_handler_allows_when_assistant() -> None:
    ss = SessionState(LanguageState(), initial_mode="assistant")
    sess = _FakeSession()
    handler = McpBridge._make_handler(
        "mcp__macos-use__macos-use_click_and_traverse", sess, None, ss
    )
    params = _CapParams({"x": 1})
    await handler(params)

    assert sess.called is True
    assert params.result["success"] is True


async def test_mcp_handler_no_gate_without_session_state() -> None:
    sess = _FakeSession()
    handler = McpBridge._make_handler("mcp__x__do", sess, None, None)
    params = _CapParams()
    await handler(params)
    assert sess.called is True  # unchanged behavior when no session_state


# --- built-in chokepoint ----------------------------------------------------


async def test_builtin_handler_blocks_bash_in_meeting(monkeypatch) -> None:
    from src.agent.tools import schemas

    called = {"v": False}

    async def _fake_execute_direct(*a, **k):
        called["v"] = True
        return {"success": True, "output": "ran"}

    monkeypatch.setattr(schemas, "execute_direct", _fake_execute_direct)

    ss = SessionState(LanguageState(), initial_mode="meeting")
    handler = schemas._make_handler(
        "bash", lambda d: str(d), None, None, ss
    )
    params = _CapParams({"command": "ls"})
    await handler(params)

    assert called["v"] is False  # execute_direct never reached
    assert params.result["success"] is False
    assert "bash is unavailable in meeting mode" in params.result["error"]


async def test_builtin_handler_allows_bash_in_ambient(monkeypatch) -> None:
    from src.agent.tools import schemas

    called = {"v": False}

    async def _fake_execute_direct(*a, **k):
        called["v"] = True
        return {"success": True, "output": "ran"}

    monkeypatch.setattr(schemas, "execute_direct", _fake_execute_direct)

    ss = SessionState(LanguageState(), initial_mode="ambient")
    handler = schemas._make_handler(
        "bash", lambda d: str(d), None, None, ss
    )
    params = _CapParams({"command": "ls"})
    await handler(params)

    assert called["v"] is True
    assert params.result["success"] is True


async def test_builtin_set_mode_exempt_even_in_meeting(monkeypatch) -> None:
    from src.agent.tools import schemas

    called = {"v": False}

    async def _fake_execute_direct(*a, **k):
        called["v"] = True
        return {"success": True, "output": "mode set"}

    monkeypatch.setattr(schemas, "execute_direct", _fake_execute_direct)

    ss = SessionState(LanguageState(), initial_mode="meeting")
    handler = schemas._make_handler(
        "set_mode", lambda d: str(d), None, None, ss
    )
    params = _CapParams({"mode": "ambient"})
    await handler(params)

    assert called["v"] is True  # exempt — runs even in meeting
    assert params.result["success"] is True


def test_meeting_profile_denies_expected_set() -> None:
    from src.agent.modes import is_tool_allowed

    p = MODE_PROFILES["meeting"]
    assert is_tool_allowed(p, "bash") is False
    assert is_tool_allowed(p, "set_mode") is True
