"""Tests for the multi-agent handler layer, tool registration, and API endpoints.

Covers all 8 agent handlers in ``src/agent/tools/direct.py``,
tool definitions in ``src/agent/tools/system.py``, and
the /api/agents REST endpoints in ``src/api.py``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import reload
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.tools import direct as direct_tools
from src.agent.tools import system as tools_system
from src.agent.subagent_manager import SubAgentState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _no_manager() -> MagicMock:
    """Return a mock that makes get_agent_manager() return None."""
    mock_get = MagicMock(return_value=None)
    return mock_get


@dataclass
class _FakeSettings:
    agent_max_concurrent: int = 5
    agent_result_ttl_seconds: float = 600.0
    agent_server_start_timeout: float = 10.0
    agent_port_range_start: int = 14100
    agent_permission_timeout_seconds: float = 120.0


def _make_state(session_id: str = "agent-01", status: str = "running") -> SubAgentState:
    return SubAgentState(
        session_id=session_id, prompt="test task", cwd=None, port=14100,
        status=status,
    )


def _make_mgr(*, start_return=None, status_return=None, result_return=None,
              message_return=None, cancel_return=None, approve_return=None,
              deny_return=None, list_return=None) -> MagicMock:
    """Build a mock SubAgentManager with standard method returns."""
    mgr = MagicMock()
    if start_return is not None:
        mgr.start = AsyncMock(return_value=start_return)
    if status_return is not None:
        mgr.status = MagicMock(return_value=status_return)
    if result_return is not None:
        mgr.result = MagicMock(return_value=result_return)
    if message_return is not None:
        mgr.message = AsyncMock(return_value=message_return)
    if cancel_return is not None:
        mgr.cancel = AsyncMock(return_value=cancel_return)
    if approve_return is not None:
        mgr.approve = AsyncMock(return_value=approve_return)
    if deny_return is not None:
        mgr.deny = AsyncMock(return_value=deny_return)
    if list_return is not None:
        mgr.list_all = MagicMock(return_value=list_return)
    return mgr


# ===================================================================
# Tests: Agent handlers (src/agent/tools/direct.py lines 3319–3464)
# ===================================================================


class TestAgentStartHandler:
    """_execute_agent_start"""

    @pytest.mark.asyncio
    async def test_valid_prompt_starts_agent(self):
        state = _make_state()
        mgr = _make_mgr(start_return=state)
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_start(
                json.dumps({"prompt": "do the thing"})
            )
        assert result["success"] is True
        assert result["session_id"] == "agent-01"
        assert result["status"] == "running"
        assert result["port"] == 14100
        mgr.start.assert_awaited_once_with("do the thing", cwd=None)

    @pytest.mark.asyncio
    async def test_valid_prompt_with_cwd(self):
        state = _make_state()
        mgr = _make_mgr(start_return=state)
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_start(
                json.dumps({"prompt": "do stuff", "cwd": "/tmp"})
            )
        assert result["success"] is True
        mgr.start.assert_awaited_once_with("do stuff", cwd="/tmp")

    @pytest.mark.asyncio
    async def test_missing_prompt(self):
        mgr = _make_mgr()
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_start(
                json.dumps({"cwd": "/tmp"})
            )
        assert result["success"] is False
        assert "prompt required" in result["error"]
        mgr.start.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_prompt(self):
        mgr = _make_mgr()
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_start(
                json.dumps({"prompt": "   "})
            )
        assert result["success"] is False
        assert "prompt required" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_json(self):
        mgr = _make_mgr()
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_start("not json")
        assert result["success"] is False
        assert "Invalid JSON" in result["error"]

    @pytest.mark.asyncio
    async def test_manager_not_initialized(self):
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=None):
            result = await direct_tools._execute_agent_start(
                json.dumps({"prompt": "test"})
            )
        assert result["success"] is False
        assert "not initialized" in result["error"]

    @pytest.mark.asyncio
    async def test_start_raises_exception(self):
        mgr = _make_mgr()
        mgr.start = AsyncMock(side_effect=RuntimeError("port conflict"))
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_start(
                json.dumps({"prompt": "test"})
            )
        assert result["success"] is False
        assert "port conflict" in result["error"]


class TestAgentStatusHandler:
    """_execute_agent_status"""

    @pytest.mark.asyncio
    async def test_valid_session_id_returns_status(self):
        status_data = {
            "session_id": "agent-01",
            "status": "running",
            "current_step": "bash: ls",
            "tool_calls": 3,
            "cost_so_far": 0.005,
            "turn": 2,
            "partial_output": "some output",
        }
        mgr = _make_mgr(status_return=status_data)
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_status(
                json.dumps({"session_id": "agent-01"})
            )
        assert result["success"] is True
        assert result["session_id"] == "agent-01"
        assert result["status"] == "running"
        mgr.status.assert_called_once_with("agent-01")

    @pytest.mark.asyncio
    async def test_missing_session_id(self):
        mgr = _make_mgr()
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_status(json.dumps({}))
        assert result["success"] is False
        assert "session_id required" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_session_id(self):
        mgr = _make_mgr()
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_status(
                json.dumps({"session_id": "  "})
            )
        assert result["success"] is False
        assert "session_id required" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_json_raises(self):
        mgr = _make_mgr()
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            with pytest.raises(json.JSONDecodeError):
                await direct_tools._execute_agent_status("not json")

    @pytest.mark.asyncio
    async def test_manager_not_initialized(self):
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=None):
            result = await direct_tools._execute_agent_status(
                json.dumps({"session_id": "agent-01"})
            )
        assert result["success"] is False
        assert "not initialized" in result["error"]

    @pytest.mark.asyncio
    async def test_status_error_from_manager(self):
        mgr = _make_mgr(status_return={"error": "Agent not found: xyz"})
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_status(
                json.dumps({"session_id": "xyz"})
            )
        assert result["success"] is False
        assert "Agent not found" in result["error"]


class TestAgentResultHandler:
    """_execute_agent_result"""

    @pytest.mark.asyncio
    async def test_valid_session_id_returns_result(self):
        result_data = {"success": True, "output": "completed work", "truncated": False}
        mgr = _make_mgr(result_return=result_data)
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_result(
                json.dumps({"session_id": "agent-01"})
            )
        assert result["success"] is True
        assert result["output"] == "completed work"
        mgr.result.assert_called_once_with("agent-01")

    @pytest.mark.asyncio
    async def test_missing_session_id(self):
        mgr = _make_mgr()
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_result(json.dumps({}))
        assert result["success"] is False
        assert "session_id required" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_json_raises(self):
        mgr = _make_mgr()
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            with pytest.raises(json.JSONDecodeError):
                await direct_tools._execute_agent_result("not json")

    @pytest.mark.asyncio
    async def test_manager_not_initialized(self):
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=None):
            result = await direct_tools._execute_agent_result(
                json.dumps({"session_id": "agent-01"})
            )
        assert result["success"] is False
        assert "not initialized" in result["error"]


class TestAgentMessageHandler:
    """_execute_agent_message"""

    @pytest.mark.asyncio
    async def test_valid_message_continues_session(self):
        msg_return = {"success": True, "status": "running", "turn": 2}
        mgr = _make_mgr(message_return=msg_return)
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_message(
                json.dumps({"session_id": "agent-01", "prompt": "continue task"})
            )
        assert result["success"] is True
        assert "spoken" in result
        assert "Continuing" in result["spoken"]["en"]
        mgr.message.assert_awaited_once_with("agent-01", "continue task")

    @pytest.mark.asyncio
    async def test_message_failure_propagates_error(self):
        msg_return = {"success": False, "error": "agent still running"}
        mgr = _make_mgr(message_return=msg_return)
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_message(
                json.dumps({"session_id": "agent-01", "prompt": "continue"})
            )
        assert result["success"] is False
        assert "still running" in result["error"]
        assert "Cannot continue" in result["spoken"]["en"]

    @pytest.mark.asyncio
    async def test_missing_session_id(self):
        mgr = _make_mgr()
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_message(
                json.dumps({"prompt": "test"})
            )
        assert result["success"] is False
        assert "session_id and prompt required" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_prompt(self):
        mgr = _make_mgr()
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_message(
                json.dumps({"session_id": "agent-01"})
            )
        assert result["success"] is False
        assert "session_id and prompt required" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_prompt_and_session(self):
        mgr = _make_mgr()
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_message(
                json.dumps({"session_id": "", "prompt": ""})
            )
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_invalid_json_raises(self):
        mgr = _make_mgr()
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            with pytest.raises(json.JSONDecodeError):
                await direct_tools._execute_agent_message("not json")

    @pytest.mark.asyncio
    async def test_manager_not_initialized(self):
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=None):
            result = await direct_tools._execute_agent_message(
                json.dumps({"session_id": "agent-01", "prompt": "test"})
            )
        assert result["success"] is False
        assert "not initialized" in result["error"]


class TestAgentCancelHandler:
    """_execute_agent_cancel"""

    @pytest.mark.asyncio
    async def test_cancel_active_agent(self):
        cancel_return = {"cancelled": True, "was_running": True}
        mgr = _make_mgr(cancel_return=cancel_return)
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_cancel(
                json.dumps({"session_id": "agent-01"})
            )
        assert result["cancelled"] is True
        assert "cancelled" in result["spoken"]["en"].lower()
        mgr.cancel.assert_awaited_once_with("agent-01")

    @pytest.mark.asyncio
    async def test_cancel_not_found(self):
        cancel_return = {"cancelled": False, "error": "not found"}
        mgr = _make_mgr(cancel_return=cancel_return)
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_cancel(
                json.dumps({"session_id": "nonexistent"})
            )
        assert result["cancelled"] is False
        assert "Cancel failed" in result["spoken"]["en"]

    @pytest.mark.asyncio
    async def test_missing_session_id(self):
        mgr = _make_mgr()
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_cancel(json.dumps({}))
        assert result["success"] is False
        assert "session_id required" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_json_raises(self):
        mgr = _make_mgr()
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            with pytest.raises(json.JSONDecodeError):
                await direct_tools._execute_agent_cancel("not json")

    @pytest.mark.asyncio
    async def test_manager_not_initialized(self):
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=None):
            result = await direct_tools._execute_agent_cancel(
                json.dumps({"session_id": "agent-01"})
            )
        assert result["success"] is False
        assert "not initialized" in result["error"]


class TestAgentListHandler:
    """_execute_agent_list"""

    @pytest.mark.asyncio
    async def test_list_returns_agents(self):
        agents = [
            {"session_id": "a", "status": "running", "current_step": ""},
            {"session_id": "b", "status": "done", "current_step": "bash: ls"},
        ]
        mgr = _make_mgr(list_return=agents)
        mgr._max_concurrent = 5
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_list("{}")
        assert result["success"] is True
        assert result["count"] == 2
        assert result["running"] == 1
        assert result["max_concurrent"] == 5
        assert len(result["agents"]) == 2
        assert "spoken" in result

    @pytest.mark.asyncio
    async def test_list_empty(self):
        mgr = _make_mgr(list_return=[])
        mgr._max_concurrent = 3
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_list("{}")
        assert result["success"] is True
        assert result["count"] == 0
        assert result["running"] == 0

    @pytest.mark.asyncio
    async def test_manager_not_initialized(self):
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=None):
            result = await direct_tools._execute_agent_list("{}")
        assert result["success"] is False
        assert "not initialized" in result["error"]


class TestAgentApproveHandler:
    """_execute_agent_approve"""

    @pytest.mark.asyncio
    async def test_approve_permission(self):
        approve_return = {"approved": True, "requestID": "r1"}
        mgr = _make_mgr(approve_return=approve_return)
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_approve(
                json.dumps({"session_id": "agent-01"})
            )
        assert result["approved"] is True
        assert result["spoken"]["en"] == "Approved."
        mgr.approve.assert_awaited_once_with("agent-01")

    @pytest.mark.asyncio
    async def test_approve_not_waiting(self):
        approve_return = {"approved": False, "error": "agent not waiting"}
        mgr = _make_mgr(approve_return=approve_return)
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_approve(
                json.dumps({"session_id": "agent-01"})
            )
        assert result["approved"] is False
        assert "not waiting" in result["spoken"]["en"]

    @pytest.mark.asyncio
    async def test_missing_session_id(self):
        mgr = _make_mgr()
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_approve(json.dumps({}))
        assert result["success"] is False
        assert "session_id required" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_json_raises(self):
        mgr = _make_mgr()
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            with pytest.raises(json.JSONDecodeError):
                await direct_tools._execute_agent_approve("not json")

    @pytest.mark.asyncio
    async def test_manager_not_initialized(self):
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=None):
            result = await direct_tools._execute_agent_approve(
                json.dumps({"session_id": "agent-01"})
            )
        assert result["success"] is False
        assert "not initialized" in result["error"]


class TestAgentDenyHandler:
    """_execute_agent_deny"""

    @pytest.mark.asyncio
    async def test_deny_permission(self):
        deny_return = {"denied": True, "corrective_sent": True}
        mgr = _make_mgr(deny_return=deny_return)
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_deny(
                json.dumps({"session_id": "agent-01"})
            )
        assert result["denied"] is True
        assert result["spoken"]["en"] == "Denied."
        mgr.deny.assert_awaited_once_with("agent-01", reason=None)

    @pytest.mark.asyncio
    async def test_deny_with_reason(self):
        deny_return = {"denied": True, "corrective_sent": True}
        mgr = _make_mgr(deny_return=deny_return)
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_deny(
                json.dumps({"session_id": "agent-01", "reason": "wrong approach"})
            )
        assert result["denied"] is True
        assert "Sent correction" in result["spoken"]["en"]
        mgr.deny.assert_awaited_once_with("agent-01", reason="wrong approach")

    @pytest.mark.asyncio
    async def test_deny_empty_reason_sent_as_none(self):
        deny_return = {"denied": True, "corrective_sent": False}
        mgr = _make_mgr(deny_return=deny_return)
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_deny(
                json.dumps({"session_id": "agent-01", "reason": ""})
            )
        assert result["denied"] is True
        mgr.deny.assert_awaited_once_with("agent-01", reason=None)

    @pytest.mark.asyncio
    async def test_missing_session_id(self):
        mgr = _make_mgr()
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            result = await direct_tools._execute_agent_deny(json.dumps({}))
        assert result["success"] is False
        assert "session_id required" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_json_raises(self):
        mgr = _make_mgr()
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            with pytest.raises(json.JSONDecodeError):
                await direct_tools._execute_agent_deny("not json")

    @pytest.mark.asyncio
    async def test_manager_not_initialized(self):
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=None):
            result = await direct_tools._execute_agent_deny(
                json.dumps({"session_id": "agent-01"})
            )
        assert result["success"] is False
        assert "not initialized" in result["error"]


# ===================================================================
# Tests: Tool registration (src/agent/tools/system.py)
# ===================================================================


class TestAgentToolDefinitions:
    """Verify all 8 agent tools exist in TOOLS and have correct metadata."""

    AGENT_TOOLS = [
        "agent_start",
        "agent_status",
        "agent_result",
        "agent_message",
        "agent_cancel",
        "agent_list",
        "agent_approve",
        "agent_deny",
    ]

    def test_all_agent_tools_present(self):
        tool_names = {t.name for t in tools_system.TOOLS}
        for at in self.AGENT_TOOLS:
            assert at in tool_names, f"{at} missing from TOOLS"

    def test_agent_start_schema(self):
        td = tools_system.get_tool("agent_start")
        assert td is not None
        assert "prompt" in td.schema_fields
        assert td.schema_fields["prompt"]["type"] == "string"
        assert td.required == ["prompt"]
        assert td.handler == "agent_start"

    def test_agent_status_schema(self):
        td = tools_system.get_tool("agent_status")
        assert td is not None
        assert "session_id" in td.schema_fields
        assert td.required == ["session_id"]
        assert td.handler == "agent_status"

    def test_agent_result_schema(self):
        td = tools_system.get_tool("agent_result")
        assert td is not None
        assert "session_id" in td.schema_fields
        assert td.required == ["session_id"]
        assert td.handler == "agent_result"

    def test_agent_message_schema(self):
        td = tools_system.get_tool("agent_message")
        assert td is not None
        assert "session_id" in td.schema_fields
        assert "prompt" in td.schema_fields
        assert td.required == ["session_id", "prompt"]
        assert td.handler == "agent_message"

    def test_agent_cancel_schema(self):
        td = tools_system.get_tool("agent_cancel")
        assert td is not None
        assert "session_id" in td.schema_fields
        assert td.required == ["session_id"]
        assert td.handler == "agent_cancel"

    def test_agent_list_schema(self):
        td = tools_system.get_tool("agent_list")
        assert td is not None
        assert td.schema_fields == {}
        assert td.required == []
        assert td.handler == "agent_list"

    def test_agent_approve_schema(self):
        td = tools_system.get_tool("agent_approve")
        assert td is not None
        assert "session_id" in td.schema_fields
        assert td.required == ["session_id"]
        assert td.handler == "agent_approve"

    def test_agent_deny_schema(self):
        td = tools_system.get_tool("agent_deny")
        assert td is not None
        assert "session_id" in td.schema_fields
        assert "reason" in td.schema_fields
        assert td.required == ["session_id"]
        assert td.handler == "agent_deny"

    def test_all_agent_tools_enabled(self):
        for at in self.AGENT_TOOLS:
            td = tools_system.get_tool(at)
            assert td is not None
            assert td.enabled is True, f"{at} should be enabled"


class TestAgentToolsReachTheirWork:
    """The worker dispatches by name, so the names have to be real.

    This used to assert `_handler_for` returned a particular function.
    That indirection was the pipeline's — it built one pipecat handler per
    tool at startup. The worker calls `execute_direct(name, ...)`, so what
    matters now is that every agent tool is in the registry and that
    `execute_direct` recognises it: a name in the registry with no branch
    behind it is a tool the model can choose and nothing can perform.
    """

    AGENT_TOOLS = (
        "agent_start",
        "agent_status",
        "agent_result",
        "agent_message",
        "agent_cancel",
        "agent_list",
        "agent_approve",
        "agent_deny",
    )

    def test_every_agent_tool_is_registered(self):
        for name in self.AGENT_TOOLS:
            assert tools_system.get_tool(name) is not None, name

    def test_every_agent_tool_has_an_implementation(self):
        import inspect

        source = inspect.getsource(direct_tools.execute_direct)
        for name in self.AGENT_TOOLS:
            assert f'"{name}"' in source, f"{name} has no branch in execute_direct"

    def test_an_unknown_tool_is_refused_not_ignored(self):
        import asyncio

        result = asyncio.run(direct_tools.execute_direct("no_such_tool", ""))
        assert result["success"] is False


# ===================================================================
# Tests: API endpoints (src/api.py — agent-related handlers)
# ===================================================================


def _mock_request(*, json_data: dict | None = None) -> MagicMock:
    """Build a mock aiohttp request with optional JSON body."""
    req = MagicMock()
    req.json = AsyncMock(return_value=json_data)
    return req


@pytest.fixture
def mock_state():
    state = MagicMock()
    state.snapshot.return_value = {"mode": "focus", "provider": "deepseek"}
    state.get_bool.return_value = False
    state.set = AsyncMock()
    state.set_bool = AsyncMock()
    return state


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.deepseek_api_key = "sk-test"
    config.zai_api_key = "sk-test"
    config.opencode_api_key = "sk-test"
    return config


@pytest.fixture
def api(mock_state, mock_config):
    from src.api import API
    return API(mock_state, mock_config)


class TestApiAgents:
    """GET /api/agents — list all managed sub-agents."""

    @pytest.mark.asyncio
    async def test_returns_agent_list(self, api):
        agents = [
            {"session_id": "a", "status": "running", "current_step": ""},
            {"session_id": "b", "status": "done", "current_step": "bash: ls"},
        ]
        mgr = MagicMock()
        mgr.list_all = MagicMock(return_value=agents)
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            request = _mock_request()
            resp = await api._handle_agents(request)
        assert resp.status == 200
        data = json.loads(resp.body)
        assert data["count"] == 2
        assert len(data["agents"]) == 2
        assert data["agents"][0]["session_id"] == "a"

    @pytest.mark.asyncio
    async def test_manager_not_initialized_returns_503(self, api):
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=None):
            request = _mock_request()
            resp = await api._handle_agents(request)
        assert resp.status == 503
        data = json.loads(resp.body)
        assert "not initialized" in data["error"]

    @pytest.mark.asyncio
    async def test_exception_returns_500(self, api):
        with patch("src.agent.subagent_manager.get_agent_manager", side_effect=RuntimeError("boom")):
            request = _mock_request()
            resp = await api._handle_agents(request)
        assert resp.status == 500


class TestApiAgentsStart:
    """POST /api/agents/start — start a new background agent."""

    @pytest.mark.asyncio
    async def test_valid_start_returns_session_id(self, api):
        state = _make_state()
        mgr = MagicMock()
        mgr.start = AsyncMock(return_value=state)
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            request = _mock_request(json_data={"prompt": "research topic"})
            resp = await api._handle_agents_start(request)
        assert resp.status == 200
        data = json.loads(resp.body)
        assert data["session_id"] == "agent-01"
        assert data["status"] == "running"
        mgr.start.assert_awaited_once_with("research topic", cwd=None)

    @pytest.mark.asyncio
    async def test_valid_start_with_cwd(self, api):
        state = _make_state()
        mgr = MagicMock()
        mgr.start = AsyncMock(return_value=state)
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            request = _mock_request(json_data={"prompt": "task", "cwd": "/workspace"})
            resp = await api._handle_agents_start(request)
        assert resp.status == 200
        mgr.start.assert_awaited_once_with("task", cwd="/workspace")

    @pytest.mark.asyncio
    async def test_missing_prompt_returns_400(self, api):
        mgr = MagicMock()
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            request = _mock_request(json_data={"cwd": "/tmp"})
            resp = await api._handle_agents_start(request)
        assert resp.status == 400
        data = json.loads(resp.body)
        assert "prompt is required" in data["error"]

    @pytest.mark.asyncio
    async def test_empty_prompt_returns_400(self, api):
        with patch("src.agent.subagent_manager.get_agent_manager"):
            request = _mock_request(json_data={"prompt": "   "})
            resp = await api._handle_agents_start(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_manager_not_initialized_returns_503(self, api):
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=None):
            request = _mock_request(json_data={"prompt": "test"})
            resp = await api._handle_agents_start(request)
        assert resp.status == 503
        data = json.loads(resp.body)
        assert "not initialized" in data["error"]

    @pytest.mark.asyncio
    async def test_start_exception_returns_500(self, api):
        mgr = MagicMock()
        mgr.start = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            request = _mock_request(json_data={"prompt": "test"})
            resp = await api._handle_agents_start(request)
        assert resp.status == 500


class TestApiAgentsCancel:
    """POST /api/agents/cancel — cancel a running agent."""

    @pytest.mark.asyncio
    async def test_cancel_with_session_id(self, api):
        mgr = MagicMock()
        mgr.cancel = AsyncMock(return_value={"cancelled": True, "was_running": True})
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            request = _mock_request(json_data={"session_id": "agent-01"})
            resp = await api._handle_agents_cancel(request)
        assert resp.status == 200
        data = json.loads(resp.body)
        assert data["cancelled"] is True
        mgr.cancel.assert_awaited_once_with("agent-01")

    @pytest.mark.asyncio
    async def test_missing_session_id_returns_400(self, api):
        with patch("src.agent.subagent_manager.get_agent_manager"):
            request = _mock_request(json_data={})
            resp = await api._handle_agents_cancel(request)
        assert resp.status == 400
        data = json.loads(resp.body)
        assert "session_id is required" in data["error"]

    @pytest.mark.asyncio
    async def test_manager_not_initialized_returns_503(self, api):
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=None):
            request = _mock_request(json_data={"session_id": "agent-01"})
            resp = await api._handle_agents_cancel(request)
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_exception_returns_500(self, api):
        mgr = MagicMock()
        mgr.cancel = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            request = _mock_request(json_data={"session_id": "agent-01"})
            resp = await api._handle_agents_cancel(request)
        assert resp.status == 500


class TestApiAgentsResult:
    """GET /api/agents/{session_id}/result — get agent output."""

    @pytest.mark.asyncio
    async def test_result_returns_output(self, api):
        mgr = MagicMock()
        mgr.result = MagicMock(return_value={
            "success": True, "output": "done", "truncated": False,
        })
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            request = _mock_request()
            request.match_info = {"session_id": "agent-01"}
            resp = await api._handle_agents_result(request)
        assert resp.status == 200
        data = json.loads(resp.body)
        assert data["success"] is True
        assert data["output"] == "done"
        mgr.result.assert_called_once_with("agent-01")

    @pytest.mark.asyncio
    async def test_result_still_running(self, api):
        mgr = MagicMock()
        mgr.result = MagicMock(return_value={
            "success": False, "output": "partial...", "truncated": True,
        })
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=mgr):
            request = _mock_request()
            request.match_info = {"session_id": "agent-01"}
            resp = await api._handle_agents_result(request)
        data = json.loads(resp.body)
        assert data["success"] is False
        assert data["truncated"] is True

    @pytest.mark.asyncio
    async def test_manager_not_initialized_returns_503(self, api):
        with patch("src.agent.subagent_manager.get_agent_manager", return_value=None):
            request = _mock_request()
            request.match_info = {"session_id": "agent-01"}
            resp = await api._handle_agents_result(request)
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_exception_returns_500(self, api):
        with patch("src.agent.subagent_manager.get_agent_manager", side_effect=RuntimeError("boom")):
            request = _mock_request()
            request.match_info = {"session_id": "agent-01"}
            resp = await api._handle_agents_result(request)
        assert resp.status == 500


class TestAgentApiRoutesRegistered:
    """Verify the 4 agent routes are registered on the aiohttp app."""

    def test_agent_routes_registered(self, api):
        pairs = []
        for r in api._app.router.resources():
            for route in r:
                pairs.append((route.method, r.canonical))
        assert ("GET", "/api/agents") in pairs
        assert ("POST", "/api/agents/start") in pairs
        assert ("POST", "/api/agents/cancel") in pairs
        assert ("GET", "/api/agents/{session_id}/result") in pairs
