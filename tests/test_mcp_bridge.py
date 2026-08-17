"""Tests for the stdio MCP client bridge.

These exercise the pure conversion / schema / handler logic with a fake
in-process session — no real subprocess or MCP server is spawned. Which
is exactly why they all passed while no MCP server could ever connect:
see ``test_the_client_the_bridge_imports_is_actually_installed``.
"""
from __future__ import annotations

import types
from typing import Any

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


async def test_call_uses_the_bare_tool_name() -> None:
    """The prefix is ours, for namespacing; the server never sees it.

    This used to go through `bridge.register`, which built one pipecat
    handler per tool. The worker calls `bridge.call(name, args)` instead,
    so that is where the prefix has to be stripped — and where a mistake
    would now show up as a server rejecting every call.
    """
    session = _FakeSession(_result([_content("done")]))
    bridge = McpBridge()
    bridge._tools.append(
        ("mcp__macos-use__type_and_traverse", "Type text", {}, session)
    )

    result = await bridge.call("mcp__macos-use__type_and_traverse", {"text": "hi"})

    assert session.calls == [("type_and_traverse", {"text": "hi"})]
    assert result == {"success": True, "output": "done"}


async def test_a_failing_server_is_reported_not_raised() -> None:
    """One broken server must cost its own tools and nothing else."""
    session = _FakeSession(raises=RuntimeError("kaboom"))
    bridge = McpBridge()
    bridge._tools.append(("mcp__x__do", "Do", {}, session))

    result = await bridge.call("mcp__x__do", {})

    assert result["success"] is False
    assert "kaboom" in result["error"]


async def test_an_unknown_tool_is_refused() -> None:
    result = await McpBridge().call("mcp__nope__missing", {})
    assert result["success"] is False


# --- register + handler -----------------------------------------------------






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

    settings = types.SimpleNamespace(mcp_dir="/tmp/whatever")
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

    settings = types.SimpleNamespace(mcp_dir="/tmp/whatever")
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


def test_the_client_the_bridge_imports_is_actually_installed() -> None:
    """The one thing every other test in this file mocks away.

    ``_connect_one`` imports these lazily and ``connect`` catches whatever
    it raises, so a missing package logged a traceback and left
    ``connected_servers`` empty — indistinguishable from "no servers
    configured". The package was never declared as a dependency, so every
    connection attempt in the project's life failed exactly that way.

    Proven end to end afterwards against a real server: engram registered,
    connected, 15 tools exposed, mem_search returning real rows.
    """
    from mcp import ClientSession, StdioServerParameters  # noqa: F401
    from mcp.client.stdio import stdio_client  # noqa: F401


# --- saying what happened ---------------------------------------------------
#
# The daemon log held no line about MCP across the project's whole life,
# during which nothing ever connected. Silence has to stop being the
# success signal.


async def test_a_missing_client_is_reported_once_not_per_server(
    monkeypatch, caplog
) -> None:
    """It is an install problem, not N broken servers."""
    import builtins

    from src.agent import mcp_bridge as mod

    monkeypatch.setattr(
        mod,
        "read_mcp_servers",
        lambda _d: {
            "a": {"command": "x"},
            "b": {"command": "y"},
            "c": {"command": "z"},
        },
    )

    real_import = builtins.__import__

    def _no_mcp(name, *args, **kwargs):
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError("No module named 'mcp'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_mcp)

    async def _never(self, slug, entry):
        raise AssertionError("must not try to spawn without a client")

    monkeypatch.setattr(McpBridge, "_connect_one", _never)

    with caplog.at_level("ERROR"):
        bridge = await connect_mcp_servers(
            types.SimpleNamespace(mcp_dir="/tmp/whatever")
        )

    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1, "one cause, one line"
    assert "not installed" in errors[0].getMessage()
    assert "3" in errors[0].getMessage(), "say how many servers it cost"
    assert bridge.connected_servers == []


async def test_a_failed_server_is_named_in_the_summary(monkeypatch, caplog) -> None:
    from src.agent import mcp_bridge as mod

    monkeypatch.setattr(
        mod, "read_mcp_servers", lambda _d: {"engram": {"command": "nope"}}
    )

    async def _boom(self, slug, entry):
        raise RuntimeError("cannot spawn")

    monkeypatch.setattr(McpBridge, "_connect_one", _boom)

    with caplog.at_level("WARNING"):
        await connect_mcp_servers(types.SimpleNamespace(mcp_dir="/tmp/whatever"))

    assert any(
        "engram" in r.getMessage() and "0 of 1" in r.getMessage()
        for r in caplog.records
        if r.levelname == "WARNING"
    )


async def test_connecting_nothing_is_still_said_out_loud(monkeypatch, caplog) -> None:
    """"No servers configured" and "could not connect" must not look alike."""
    from src.agent import mcp_bridge as mod

    monkeypatch.setattr(mod, "read_mcp_servers", lambda _d: {})

    with caplog.at_level("INFO"):
        await connect_mcp_servers(types.SimpleNamespace(mcp_dir="/tmp/whatever"))

    assert any("no servers configured" in r.getMessage() for r in caplog.records)
