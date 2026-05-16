"""Tests for the stdio MCP client bridge.

These exercise the pure conversion / schema / handler logic with a fake
in-process session — no real subprocess or MCP server is spawned.
"""
from __future__ import annotations

import types
from typing import Any

import pytest

from src.agent.mcp_bridge import (
    McpBridge,
    _normalise_call_result,
    connect_mcp_servers,
)


def _content(text: str) -> Any:
    return types.SimpleNamespace(text=text)


def _result(content, is_error=False, structured=None) -> Any:
    return types.SimpleNamespace(
        content=content, isError=is_error, structuredContent=structured
    )


class _FakeSession:
    def __init__(self, result: Any = None, raises: Exception | None = None):
        self._result = result
        self._raises = raises
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict) -> Any:
        self.calls.append((name, arguments))
        if self._raises is not None:
            raise self._raises
        return self._result


class _CapturingParams:
    def __init__(self, arguments: dict):
        self.arguments = arguments
        self.result: Any = None

    async def result_callback(self, result: Any) -> None:
        self.result = result


# --- _normalise_call_result -------------------------------------------------


def test_normalise_concatenates_text_blocks() -> None:
    out = _normalise_call_result(
        _result([_content("hello"), _content("world")])
    )
    assert out == {"success": True, "output": "hello\nworld"}


def test_normalise_marks_error() -> None:
    out = _normalise_call_result(_result([_content("boom")], is_error=True))
    assert out["success"] is False
    assert out["error"] == "boom"


def test_normalise_passes_structured_through() -> None:
    out = _normalise_call_result(
        _result([_content("ok")], structured={"pid": 42})
    )
    assert out["structured"] == {"pid": 42}


# --- function_schemas -------------------------------------------------------


def test_function_schemas_naming_and_shape() -> None:
    bridge = McpBridge()
    bridge._tools.append(
        (
            "mcp__macos-use__click_and_traverse",
            "Click at coords",
            {
                "type": "object",
                "properties": {"x": {"type": "number"}},
                "required": ["x"],
            },
            _FakeSession(),
        )
    )
    schemas = bridge.function_schemas()
    assert len(schemas) == 1
    assert schemas[0].name == "mcp__macos-use__click_and_traverse"
    assert "x" in schemas[0].properties
    assert schemas[0].required == ["x"]


# --- register + handler -----------------------------------------------------


async def test_handler_calls_session_with_bare_tool_name() -> None:
    session = _FakeSession(_result([_content("done")]))
    bridge = McpBridge()
    bridge._tools.append(
        ("mcp__macos-use__type_and_traverse", "Type text", {}, session)
    )

    captured: dict[str, Any] = {}

    class _LLM:
        def register_function(self, name, handler, **kw):
            captured[name] = handler

    names = bridge.register(_LLM())
    assert names == ["mcp__macos-use__type_and_traverse"]

    params = _CapturingParams({"text": "hi"})
    await captured["mcp__macos-use__type_and_traverse"](params)

    # Server sees the bare tool name, not the mcp__ prefix.
    assert session.calls == [("type_and_traverse", {"text": "hi"})]
    assert params.result == {"success": True, "output": "done"}


async def test_handler_error_path_returns_failure() -> None:
    session = _FakeSession(raises=RuntimeError("kaboom"))
    bridge = McpBridge()
    bridge._tools.append(("mcp__x__do", "do", {}, session))

    holder: dict[str, Any] = {}

    class _LLM:
        def register_function(self, name, handler, **kw):
            holder["h"] = handler

    bridge.register(_LLM())
    params = _CapturingParams({})
    await holder["h"](params)

    assert params.result["success"] is False
    assert "kaboom" in params.result["error"]


# --- resilience / lifecycle -------------------------------------------------


async def test_connect_never_raises_on_bad_config(monkeypatch) -> None:
    """A server that explodes on connect must not break daemon startup."""
    from src.agent import mcp_bridge as mod

    monkeypatch.setattr(
        mod,
        "read_mcp_servers",
        lambda _ws: {"broken": {"command": "nope", "args": []}},
    )

    async def _boom(self, slug, entry):
        raise RuntimeError("cannot spawn")

    monkeypatch.setattr(McpBridge, "_connect_one", _boom)

    settings = types.SimpleNamespace(workspace_dir="/tmp/whatever")
    bridge = await connect_mcp_servers(settings)
    assert bridge.connected_servers == []
    assert bridge.tool_names == []


async def test_disabled_server_skipped(monkeypatch) -> None:
    from src.agent import mcp_bridge as mod

    monkeypatch.setattr(
        mod,
        "read_mcp_servers",
        lambda _ws: {"macos-use": {"command": "npx", "disabled": True}},
    )
    called = False

    async def _connect(self, slug, entry):
        nonlocal called
        called = True

    monkeypatch.setattr(McpBridge, "_connect_one", _connect)

    settings = types.SimpleNamespace(workspace_dir="/tmp/whatever")
    bridge = await connect_mcp_servers(settings)
    assert called is False
    assert bridge.connected_servers == []


async def test_aclose_is_safe_on_empty_bridge() -> None:
    bridge = McpBridge()
    await bridge.aclose()  # must not raise


# --- prompt_block -----------------------------------------------------------


def test_prompt_block_lists_live_tools() -> None:
    bridge = McpBridge()
    sess = _FakeSession()
    bridge._tools += [
        ("mcp__macos-use__open_app", "open", {}, sess),
        ("mcp__macos-use__click", "click", {}, sess),
    ]
    block = bridge.prompt_block()
    assert "Connected MCP servers (1)" in block
    assert "macos-use (2 tools)" in block
    assert "mcp__macos-use__open_app" in block
    assert "mcp__macos-use__click" in block
    assert "RIGHT NOW" in block


def test_prompt_block_empty_when_nothing_connected() -> None:
    assert McpBridge().prompt_block() == ""
