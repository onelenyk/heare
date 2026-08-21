"""MCP on the spine: the tools reach the worker, and only the worker.

No network, no subprocess, no real MCP server — a fake bridge stands in
for the sessions. What is being checked is the wiring: which registry
carries an ``mcp__*`` name to ``Hands``, that a server which fails to
start costs nothing but its own tools, that a role's deny list still
bites once the tools exist, and that the prompt says what is connected.
"""
from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import pytest

from src.agent.hands import Hands
from src.agent.mcp_bridge import McpBridge, connect_mcp_servers
from src.spine.prompt import build_system_prompt
from src.spine.tools import McpHands, make_hands_factory


class _FakeSession:
    """An MCP session that never leaves the process."""

    def __init__(self, output: str = "done", raises: Exception | None = None):
        self._output = output
        self._raises = raises
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict) -> Any:
        self.calls.append((name, arguments))
        if self._raises is not None:
            raise self._raises
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(text=self._output)],
            isError=False,
            structuredContent=None,
        )


def _bridge_with(*names: str, session: Any = None) -> McpBridge:
    """A connected-looking bridge, without connecting to anything."""
    bridge = McpBridge()
    sess = session or _FakeSession()
    for name in names:
        slug = name.split("__", 2)[1]
        bridge._tools.append(
            (
                name,
                f"{name} description",
                {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                sess,
            )
        )
        if slug not in bridge._connected_servers:
            bridge._connected_servers.append(slug)
    return bridge


@pytest.fixture(autouse=True)
def _clean_registry():
    """The registry is process-wide; a leaked tool would follow the suite."""
    McpBridge.unregister_worker_tools()
    yield
    McpBridge.unregister_worker_tools()


def _settings() -> Any:
    return types.SimpleNamespace(capability_install_enabled=False)


def _profile(*denied: str) -> Any:
    from src.agent.tool_gate import OPEN, ToolPolicy

    if not denied:
        return OPEN
    return ToolPolicy(name="роль «тест»", denied_tool_patterns=tuple(denied))


def _names(schemas: list[dict]) -> list[str]:
    return [s["function"]["name"] for s in schemas]


# --- the mechanism: which registry carries the tools ------------------------


def test_worker_sees_mcp_tools_after_the_bridge_registers() -> None:
    """``system.TOOLS`` is a module-level list — that is the whole carry."""
    worker = Hands(_settings())
    assert not [n for n in _names(worker._tool_schemas()) if n.startswith("mcp__")]

    bridge = _bridge_with("mcp__files__read_file", "mcp__files__list_directory")
    added = bridge.register_worker_tools()

    assert added == ["mcp__files__read_file", "mcp__files__list_directory"]
    names = _names(worker._tool_schemas())
    assert "mcp__files__read_file" in names
    assert "mcp__files__list_directory" in names


def test_a_worker_built_before_the_bridge_still_sees_the_tools() -> None:
    """The daemon builds the worker first and connects MCP after it."""
    worker = Hands(_settings())
    _bridge_with("mcp__files__read_file").register_worker_tools()
    assert "mcp__files__read_file" in _names(worker._tool_schemas())


def test_the_schema_keeps_the_servers_required_list() -> None:
    """ToolDef's "empty required means all required" rule must not apply."""
    bridge = McpBridge()
    bridge._tools.append(
        (
            "mcp__files__search",
            "search",
            {
                "type": "object",
                "properties": {"q": {"type": "string"}, "limit": {"type": "number"}},
                "required": ["q"],
            },
            _FakeSession(),
        )
    )
    bridge.register_worker_tools()
    schema = next(
        s
        for s in Hands(_settings())._tool_schemas()
        if s["function"]["name"] == "mcp__files__search"
    )
    assert schema["function"]["parameters"]["required"] == ["q"]
    assert set(schema["function"]["parameters"]["properties"]) == {"q", "limit"}


async def test_closing_the_bridge_takes_the_tools_back_out() -> None:
    bridge = _bridge_with("mcp__files__read_file")
    bridge.register_worker_tools()
    await bridge.aclose()
    assert not [
        n for n in _names(Hands(_settings())._tool_schemas()) if n.startswith("mcp__")
    ]


def test_registering_twice_does_not_duplicate() -> None:
    bridge = _bridge_with("mcp__files__read_file")
    bridge.register_worker_tools()
    bridge.register_worker_tools()
    names = _names(Hands(_settings())._tool_schemas())
    assert names.count("mcp__files__read_file") == 1


# --- fail soft --------------------------------------------------------------


async def test_a_server_that_will_not_start_leaves_a_working_assistant(
    monkeypatch,
) -> None:
    from src.agent import mcp_bridge as mod

    monkeypatch.setattr(
        mod, "read_mcp_servers", lambda _d: {"broken": {"command": "nope"}}
    )

    async def _boom(self, slug, entry):
        raise RuntimeError("cannot spawn")

    monkeypatch.setattr(McpBridge, "_connect_one", _boom)

    bridge = await connect_mcp_servers(types.SimpleNamespace(mcp_dir="/tmp/nowhere"))
    assert bridge.register_worker_tools() == []

    names = _names(Hands(_settings())._tool_schemas())
    assert not [n for n in names if n.startswith("mcp__")]
    # The rest of the toolbox is untouched: an assistant, minus MCP.
    assert "bash" in names and "read" in names


async def test_a_missing_config_file_is_not_an_error(monkeypatch, tmp_path) -> None:
    bridge = await connect_mcp_servers(
        types.SimpleNamespace(mcp_dir=tmp_path / "nothing-here")
    )
    assert bridge.connected_servers == []
    assert bridge.register_worker_tools() == []


async def test_calling_a_tool_no_server_offers_is_an_answer_not_a_crash() -> None:
    result = await McpBridge().call("mcp__files__read_file", {"path": "/tmp/x"})
    assert result["success"] is False
    assert "no connected MCP server" in result["error"]


# --- the role gate ----------------------------------------------------------


def _meeting_deny_tools() -> tuple[str, ...]:
    """The deny list as it is actually written in roles/meeting.md."""
    from src.spine.roles import RoleLoader

    roles = RoleLoader([Path(__file__).resolve().parent.parent / "roles"]).load()
    meeting = next(
        r for r in roles.values() if "mcp__*" in tuple(getattr(r, "deny_tools", ()))
    )
    return tuple(meeting.deny_tools)


def test_meeting_role_hides_mcp_tools_from_the_worker() -> None:
    _bridge_with("mcp__files__read_file").register_worker_tools()

    session_state = types.SimpleNamespace(policy=_profile(*_meeting_deny_tools()))
    worker = Hands(_settings(), session_state=session_state)
    names = _names(worker._tool_schemas())

    assert not [n for n in names if n.startswith("mcp__")]
    assert "bash" not in names  # the rest of the role's deny list still holds
    assert "read" in names  # and it is a deny list, not an allow list


async def test_meeting_role_refuses_an_mcp_call_it_never_advertised() -> None:
    """Defense in depth: the model can name a tool it was not shown."""
    session = _FakeSession()
    bridge = _bridge_with("mcp__files__read_file", session=session)
    bridge.register_worker_tools()

    session_state = types.SimpleNamespace(policy=_profile(*_meeting_deny_tools()))
    worker = McpHands(
        _settings(), session_state=session_state, mcp_provider=lambda: bridge
    )
    out = await worker._execute("mcp__files__read_file", {"path": "/tmp/x"})

    assert "refused" in out
    assert session.calls == []


# --- the worker actually calls them -----------------------------------------


async def test_the_worker_calls_the_server_with_the_bare_tool_name() -> None:
    session = _FakeSession(output="hello from the server")
    bridge = _bridge_with("mcp__files__read_file", session=session)
    worker = McpHands(_settings(), mcp_provider=lambda: bridge)

    out = await worker._execute("mcp__files__read_file", {"path": "/tmp/x"})

    assert session.calls == [("read_file", {"path": "/tmp/x"})]
    assert out == "hello from the server"


async def test_a_failing_mcp_call_comes_back_as_text_not_an_exception() -> None:
    session = _FakeSession(raises=RuntimeError("kaboom"))
    bridge = _bridge_with("mcp__files__read_file", session=session)
    worker = McpHands(_settings(), mcp_provider=lambda: bridge)

    out = await worker._execute("mcp__files__read_file", {"path": "/tmp/x"})
    assert "failed" in out and "kaboom" in out


async def test_without_a_bridge_the_worker_says_so() -> None:
    worker = McpHands(_settings(), mcp_provider=lambda: None)
    out = await worker._execute("mcp__files__read_file", {})
    assert "no MCP servers are connected" in out


async def test_built_in_tools_still_go_the_normal_way(monkeypatch) -> None:
    """The subclass must not change what happens to everything else."""
    seen: dict[str, Any] = {}

    async def _fake_direct(name, args, settings):
        seen["call"] = (name, args)
        return {"success": True, "output": "ok"}

    monkeypatch.setattr("src.agent.tools.direct.execute_direct", _fake_direct)
    worker = McpHands(_settings(), mcp_provider=lambda: _bridge_with("mcp__a__b"))
    out = await worker._execute("bash", {"command": "echo hi"})

    assert out == "ok"
    assert seen["call"][0] == "bash"


def test_the_factory_builds_a_gated_mcp_capable_worker() -> None:
    bridge = _bridge_with("mcp__files__read_file")
    state = types.SimpleNamespace(policy=_profile())
    worker = make_hands_factory(
        session_state=state, mcp_provider=lambda: bridge
    )(_settings())
    assert isinstance(worker, McpHands)
    assert worker._session_state is state
    assert worker._mcp_provider() is bridge


# --- the prompt -------------------------------------------------------------


def test_prompt_carries_the_block_when_servers_are_connected() -> None:
    block = _bridge_with("mcp__files__read_file").prompt_block()
    prompt = build_system_prompt(persona="Я heare.", mcp_block=block)
    assert "mcp__files__read_file" in prompt
    assert prompt.index("Я heare.") < prompt.index("mcp__files__read_file")


def test_prompt_has_no_empty_mcp_section_when_nothing_connected() -> None:
    assert McpBridge().prompt_block() == ""
    with_block = build_system_prompt(persona="Я heare.", mcp_block="")
    assert "MCP" not in with_block
    assert with_block == build_system_prompt(persona="Я heare.")
