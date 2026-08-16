"""Tests for the multi-agent background sub-agent system.

Covers SubAgentState, SubAgentManager lifecycle, SSE event parsing,
permission gating, concurrency control, and cleanup.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.subagent_manager import (
    SubAgentManager,
    SubAgentState,
    get_agent_manager,
    set_agent_manager,
)


@dataclass
class _FakeSettings:
    agent_max_concurrent: int = 5
    agent_result_ttl_seconds: float = 600.0
    agent_server_start_timeout: float = 10.0
    agent_port_range_start: int = 14100
    agent_permission_timeout_seconds: float = 120.0


class TestSubAgentState:
    def test_defaults(self):
        state = SubAgentState(session_id="test-1", prompt="hello", cwd=None, port=14100)
        assert state.session_id == "test-1"
        assert state.prompt == "hello"
        assert state.cwd is None
        assert state.port == 14100
        assert state.status == "starting"
        assert state.server_process is None
        assert state.output_parts == []
        assert state.events == []
        assert state.cost is None
        assert state.tokens is None
        assert state.tool_calls == 0
        assert state.current_step == ""
        assert state.error_message is None
        assert state.pending_permission is None
        assert state.pending_since is None
        assert state.started_at == 0.0
        assert state.finished_at is None
        assert state.turn == 1
        assert state._sse_task is None
        assert state._slot_released is False

    def test_field_types(self):
        state = SubAgentState(
            session_id="sid", prompt="p", cwd="/tmp", port=9999,
            status="running", tool_calls=5, cost=0.01, turn=3,
        )
        assert isinstance(state.cost, float)
        assert isinstance(state.tool_calls, int)
        assert isinstance(state.turn, int)
        assert isinstance(state.output_parts, list)
        assert isinstance(state.events, list)


class TestSubAgentManagerInit:
    def test_defaults_without_settings(self):
        mgr = SubAgentManager()
        assert mgr._max_concurrent == 5
        assert mgr._ttl_seconds == 600.0
        assert mgr._start_timeout == 10.0
        assert mgr._port_range_start == 14100
        assert mgr._port_range_end == 14200
        assert mgr._permission_timeout == 120.0
        assert mgr._semaphore._value == 5

    def test_custom_settings(self):
        s = _FakeSettings(
            agent_max_concurrent=3,
            agent_result_ttl_seconds=60.0,
            agent_server_start_timeout=5.0,
            agent_port_range_start=15000,
            agent_permission_timeout_seconds=30.0,
        )
        mgr = SubAgentManager(s)
        assert mgr._max_concurrent == 3
        assert mgr._ttl_seconds == 60.0
        assert mgr._start_timeout == 5.0
        assert mgr._port_range_start == 15000
        assert mgr._port_range_end == 15100
        assert mgr._permission_timeout == 30.0
        assert mgr._semaphore._value == 3

    def test_singleton_pattern(self):
        SubAgentManager._instance = None
        assert SubAgentManager.get() is None

        mgr = SubAgentManager()
        SubAgentManager.set(mgr)
        assert SubAgentManager.get() is mgr

        set_agent_manager(mgr)
        assert get_agent_manager() is mgr

        SubAgentManager._instance = None

    def test_client_lazy_init(self):
        mgr = SubAgentManager()
        assert mgr._client is None


class TestFindFreePort:
    def test_returns_first_available_port(self):
        mgr = SubAgentManager(_FakeSettings(agent_port_range_start=14100))
        port = mgr._find_free_port()
        assert 14100 <= port < 14200

    def test_skips_used_ports(self):
        mgr = SubAgentManager(_FakeSettings(agent_port_range_start=14100))
        state = SubAgentState(session_id="x", prompt="p", cwd=None, port=14100)
        mgr._agents["x"] = state
        port = mgr._find_free_port()
        assert port != 14100

    def test_range_exhaustion(self):
        mgr = SubAgentManager(_FakeSettings(agent_port_range_start=14100))
        for p in range(14100, 14200):
            mgr._agents[str(p)] = SubAgentState(
                session_id=str(p), prompt="p", cwd=None, port=p,
            )

        with pytest.raises(RuntimeError, match="No free ports"):
            mgr._find_free_port()


class TestConcurrencyControl:
    @pytest.mark.asyncio
    async def test_semaphore_blocks_when_full(self):
        mgr = SubAgentManager(_FakeSettings(agent_max_concurrent=0))
        with pytest.raises(RuntimeError, match="Max 0 concurrent"):
            await mgr.start("test")

    def test_release_slot_idempotent(self):
        mgr = SubAgentManager(_FakeSettings(agent_max_concurrent=3))
        state = SubAgentState(session_id="s", prompt="p", cwd=None, port=14100)

        initial_value = mgr._semaphore._value
        mgr._release_slot(state)
        assert mgr._semaphore._value == initial_value + 1
        mgr._release_slot(state)
        assert mgr._semaphore._value == initial_value + 1

    def test_release_slot_marks_state(self):
        mgr = SubAgentManager()
        state = SubAgentState(session_id="s", prompt="p", cwd=None, port=14100)
        assert state._slot_released is False
        mgr._release_slot(state)
        assert state._slot_released is True


class TestQueryMethods:
    def test_status_not_found(self):
        mgr = SubAgentManager()
        assert mgr.status("nonexistent") == {"error": "Agent not found: nonexistent"}

    def test_status_running(self):
        mgr = SubAgentManager()
        state = SubAgentState(
            session_id="s1", prompt="do stuff", cwd=None, port=14100,
            status="running", current_step="bash: ls", tool_calls=3,
            cost=0.005, turn=2,
        )
        state.output_parts.append("output text")
        mgr._agents["s1"] = state

        result = mgr.status("s1")
        assert result["session_id"] == "s1"
        assert result["status"] == "running"
        assert result["current_step"] == "bash: ls"
        assert result["tool_calls"] == 3
        assert result["cost_so_far"] == 0.005
        assert result["turn"] == 2
        assert "partial_output" in result

    def test_status_waiting_for_input(self):
        mgr = SubAgentManager()
        state = SubAgentState(
            session_id="s1", prompt="do stuff", cwd=None, port=14100,
            status="waiting_for_input",
            pending_permission={"tool": "bash", "requestID": "r1"},
            pending_since=time.time(),
        )
        mgr._agents["s1"] = state

        result = mgr.status("s1")
        assert result["status"] == "waiting_for_input"
        assert result["permission"] == {"tool": "bash", "requestID": "r1"}
        assert "waiting_seconds" in result

    def test_status_done(self):
        mgr = SubAgentManager()
        state = SubAgentState(
            session_id="s1", prompt="do stuff", cwd=None, port=14100,
            status="done", finished_at=time.time(),
        )
        state.output_parts.append("final result")
        mgr._agents["s1"] = state

        result = mgr.status("s1")
        assert result["status"] == "done"
        assert result["output"] == "final result"
        assert "finished_at" in result

    def test_status_error(self):
        mgr = SubAgentManager()
        state = SubAgentState(
            session_id="s1", prompt="do stuff", cwd=None, port=14100,
            status="error", error_message="something broke",
        )
        mgr._agents["s1"] = state

        result = mgr.status("s1")
        assert result["status"] == "error"
        assert result["error_message"] == "something broke"

    def test_result_not_found(self):
        mgr = SubAgentManager()
        result = mgr.result("nonexistent")
        assert result["success"] is False

    def test_result_done(self):
        mgr = SubAgentManager()
        state = SubAgentState(
            session_id="s1", prompt="do", cwd=None, port=14100, status="done",
        )
        state.output_parts.append("completed output")
        mgr._agents["s1"] = state

        result = mgr.result("s1")
        assert result["success"] is True
        assert result["output"] == "completed output"
        assert result["truncated"] is False

    def test_result_running_truncated_flag(self):
        mgr = SubAgentManager()
        state = SubAgentState(
            session_id="s1", prompt="do", cwd=None, port=14100, status="running",
        )
        state.output_parts.append("x" * 100001)
        mgr._agents["s1"] = state

        result = mgr.result("s1")
        assert result["success"] is False
        assert result["truncated"] is True
        assert len(result["output"]) == 100000

    def test_list_all(self):
        mgr = SubAgentManager()
        mgr._agents["a"] = SubAgentState(
            session_id="a", prompt="pa", cwd=None, port=14100, status="running",
        )
        mgr._agents["b"] = SubAgentState(
            session_id="b", prompt="pb", cwd=None, port=14101, status="done",
        )
        assert len(mgr.list_all()) == 2

    def test_list_active_filters_by_status(self):
        mgr = SubAgentManager(_FakeSettings(agent_result_ttl_seconds=0.0))
        mgr._agents["a"] = SubAgentState(
            session_id="a", prompt="pa", cwd=None, port=14100, status="running",
        )
        mgr._agents["b"] = SubAgentState(
            session_id="b", prompt="pb", cwd=None, port=14101, status="done",
            finished_at=time.time() - 9999,
        )

        active = mgr.list_active()
        assert len(active) == 1
        assert active[0]["session_id"] == "a"


class TestCancel:
    @pytest.mark.asyncio
    async def test_cancel_not_found(self):
        mgr = SubAgentManager()
        result = await mgr.cancel("nonexistent")
        assert result["cancelled"] is False

    @pytest.mark.asyncio
    async def test_cancel_active_agent_releases_slot(self):
        mgr = SubAgentManager(_FakeSettings(agent_max_concurrent=3))
        initial = mgr._semaphore._value
        state = SubAgentState(
            session_id="s1", prompt="do", cwd=None, port=14100, status="running",
        )
        mgr._agents["s1"] = state

        result = await mgr.cancel("s1")
        assert result["cancelled"] is True
        assert result["was_running"] is True
        assert state.status == "cancelled"
        assert state.finished_at is not None
        assert mgr._semaphore._value == initial + 1

    @pytest.mark.asyncio
    async def test_cancel_terminal_agent_no_double_release(self):
        mgr = SubAgentManager(_FakeSettings(agent_max_concurrent=3))
        initial = mgr._semaphore._value
        state = SubAgentState(
            session_id="s1", prompt="do", cwd=None, port=14100,
            status="done", finished_at=time.time(), _slot_released=True,
        )
        mgr._agents["s1"] = state

        result = await mgr.cancel("s1")
        assert result["cancelled"] is True
        assert result["was_running"] is False
        assert mgr._semaphore._value == initial


class TestPermissionGating:
    @pytest.mark.asyncio
    async def test_approve_not_waiting(self):
        mgr = SubAgentManager()
        state = SubAgentState(
            session_id="s1", prompt="do", cwd=None, port=14100, status="running",
        )
        mgr._agents["s1"] = state

        result = await mgr.approve("s1")
        assert result["approved"] is False

    @pytest.mark.asyncio
    async def test_deny_not_waiting(self):
        mgr = SubAgentManager()
        state = SubAgentState(
            session_id="s1", prompt="do", cwd=None, port=14100, status="running",
        )
        mgr._agents["s1"] = state

        result = await mgr.deny("s1")
        assert result["denied"] is False

    @pytest.mark.asyncio
    async def test_deny_with_reason_sends_correction(self):
        mgr = SubAgentManager()
        state = SubAgentState(
            session_id="s1", prompt="do", cwd=None, port=14100,
            status="waiting_for_input",
            pending_permission={"requestID": "req-1", "tool": "bash"},
        )
        mgr._agents["s1"] = state

        with patch.object(mgr, "_get_client") as mock_client:
            mock_http = AsyncMock()
            mock_http.post.return_value = AsyncMock()
            mock_http.post.return_value.raise_for_status = MagicMock()
            mock_client.return_value = mock_http

            result = await mgr.deny("s1", reason="don't do that")
            assert result["denied"] is True
            assert result["corrective_sent"] is True
            assert state.status == "running"
            assert state.pending_permission is None


class TestMessage:
    @pytest.mark.asyncio
    async def test_message_on_running_agent_rejected(self):
        mgr = SubAgentManager()
        state = SubAgentState(
            session_id="s1", prompt="do", cwd=None, port=14100, status="running",
        )
        mgr._agents["s1"] = state

        result = await mgr.message("s1", "continue")
        assert result["success"] is False
        assert "still running" in result["error"]

    @pytest.mark.asyncio
    async def test_message_on_done_agent_resets_state(self):
        mgr = SubAgentManager()
        state = SubAgentState(
            session_id="s1", prompt="do", cwd=None, port=14100,
            status="done", finished_at=time.time(), turn=2,
            tool_calls=5, cost=0.01,
        )
        state.output_parts.append("previous")
        mgr._agents["s1"] = state

        with patch.object(mgr, "_get_client") as mock_client:
            mock_http = AsyncMock()
            mock_http.post.return_value = AsyncMock()
            mock_http.post.return_value.raise_for_status = MagicMock()
            mock_client.return_value = mock_http

            result = await mgr.message("s1", "new prompt")
            assert result["success"] is True
            assert result["status"] == "running"
            assert result["turn"] == 3
            assert state.tool_calls == 0
            assert state.cost is None
            assert state.output_parts == []
            assert state.current_step == ""


class TestSSEParsing:
    def _make_manager(self) -> SubAgentManager:
        mgr = SubAgentManager(_FakeSettings())
        return mgr

    def _setup_sse_stream(self, mgr: SubAgentManager, lines: list[str]):
        async def _lines():
            for line in lines:
                yield line

        mock_resp = AsyncMock()
        mock_resp.aiter_lines = _lines

        async def _stream_ctx():
            return mock_resp

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(side_effect=_stream_ctx)
        mock_stream.__aexit__ = AsyncMock(return_value=None)

        mgr._client = MagicMock()
        mgr._client.stream = MagicMock(return_value=mock_stream)

    @pytest.mark.asyncio
    async def test_stream_end_sets_done_and_releases(self):
        mgr = self._make_manager()
        initial = mgr._semaphore._value
        state = SubAgentState(
            session_id="s1", prompt="do", cwd=None, port=14100, status="running",
        )

        self._setup_sse_stream(mgr, [])
        await mgr._listen_sse(state)

        assert state.status == "done"
        assert state.finished_at is not None
        assert mgr._semaphore._value == initial + 1
        assert state._slot_released is True

    @pytest.mark.asyncio
    async def test_permission_asked_sets_waiting(self):
        mgr = self._make_manager()
        state = SubAgentState(
            session_id="s1", prompt="do", cwd=None, port=14100, status="running",
        )

        lines = [
            'data: {"type":"permission.asked","properties":{"requestID":"r1","tool":"bash","input":{}}}',
        ]
        self._setup_sse_stream(mgr, lines)
        await mgr._listen_sse(state)

        assert len(state.events) == 1
        assert state.events[0]["type"] == "permission.asked"

    @pytest.mark.asyncio
    async def test_waiting_for_input_event(self):
        mgr = self._make_manager()
        state = SubAgentState(
            session_id="s1", prompt="do", cwd=None, port=14100, status="running",
        )

        lines = ['data: {"type":"waiting_for_input","properties":{}}']
        self._setup_sse_stream(mgr, lines)
        await mgr._listen_sse(state)
        assert state.pending_since is not None

    @pytest.mark.asyncio
    async def test_session_idle_sets_done(self):
        mgr = self._make_manager()
        state = SubAgentState(
            session_id="s1", prompt="do", cwd=None, port=14100, status="running",
        )

        lines = ['data: {"type":"session.idle","properties":{}}']
        self._setup_sse_stream(mgr, lines)
        await mgr._listen_sse(state)
        assert state.status == "done"

    @pytest.mark.asyncio
    async def test_text_accumulation(self):
        mgr = self._make_manager()
        state = SubAgentState(
            session_id="s1", prompt="do", cwd=None, port=14100, status="running",
        )

        lines = [
            'data: {"type":"message.part.updated","properties":{"part":{"type":"text","text":"hello"}}}',
            'data: {"type":"message.part.delta","properties":{"delta":" world"}}',
        ]
        self._setup_sse_stream(mgr, lines)
        await mgr._listen_sse(state)
        assert state.output_parts == ["hello", " world"]

    @pytest.mark.asyncio
    async def test_tool_completed_increments_count(self):
        mgr = self._make_manager()
        state = SubAgentState(
            session_id="s1", prompt="do", cwd=None, port=14100, status="running",
        )

        lines = [
            'data: {"type":"message.part.updated","properties":{"part":{"type":"tool","tool":"bash","state":{"status":"completed","input":{}}}}}',
        ]
        self._setup_sse_stream(mgr, lines)
        await mgr._listen_sse(state)
        assert state.tool_calls == 1

    @pytest.mark.asyncio
    async def test_step_finish_accumulates_cost(self):
        mgr = self._make_manager()
        state = SubAgentState(
            session_id="s1", prompt="do", cwd=None, port=14100, status="running",
        )

        lines = [
            'data: {"type":"message.part.updated","properties":{"part":{"type":"step-finish","cost":0.01,"tokens":{"input":100,"output":50}}}}',
        ]
        self._setup_sse_stream(mgr, lines)
        await mgr._listen_sse(state)
        assert state.cost == 0.01
        assert state.tokens == {"input": 100, "output": 50}

    @pytest.mark.asyncio
    async def test_connection_error_retries_then_errors(self):
        mgr = self._make_manager()
        mgr._client = MagicMock()
        mgr._client.stream.side_effect = ConnectionError("refused")
        state = SubAgentState(
            session_id="s1", prompt="do", cwd=None, port=14100, status="running",
        )

        await mgr._listen_sse(state)

        assert state.status == "error"
        assert "SSE connection lost" in (state.error_message or "")

    @pytest.mark.asyncio
    async def test_skips_non_data_lines(self):
        mgr = self._make_manager()
        state = SubAgentState(
            session_id="s1", prompt="do", cwd=None, port=14100, status="running",
        )

        lines = [
            ': heartbeat\n',
            'data: {"type":"message.part.delta","properties":{"delta":"ok"}}\n',
            '\n',
        ]
        self._setup_sse_stream(mgr, lines)
        await mgr._listen_sse(state)
        assert state.output_parts == ["ok"]
        assert len(state.events) == 1

    @pytest.mark.asyncio
    async def test_bad_json_skipped(self):
        mgr = self._make_manager()
        state = SubAgentState(
            session_id="s1", prompt="do", cwd=None, port=14100, status="running",
        )

        lines = [
            'data: not json\n',
            'data: {"type":"message.part.delta","properties":{"delta":"good"}}\n',
        ]
        self._setup_sse_stream(mgr, lines)
        await mgr._listen_sse(state)
        assert len(state.events) == 1
        assert state.events[0]["type"] == "message.part.delta"


class TestPruneLoop:
    @pytest.mark.asyncio
    async def test_prunes_expired_agents(self):
        mgr = SubAgentManager(_FakeSettings(agent_result_ttl_seconds=0.0))
        state = SubAgentState(
            session_id="s1", prompt="do", cwd=None, port=14100,
            status="done", finished_at=time.time() - 9999,
        )
        mgr._agents["s1"] = state

        with patch.object(asyncio, "sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = [None, asyncio.CancelledError()]
            task = asyncio.create_task(mgr._prune_loop())
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert "s1" not in mgr._agents

    @pytest.mark.asyncio
    async def test_permission_timeout_auto_denies(self):
        mgr = SubAgentManager(_FakeSettings(agent_permission_timeout_seconds=0.0))
        state = SubAgentState(
            session_id="s1", prompt="do", cwd=None, port=14100,
            status="waiting_for_input",
            pending_permission={"requestID": "r1"},
            pending_since=time.time() - 9999,
        )
        mgr._agents["s1"] = state

        # The prune loop's auto-deny calls mgr.deny(), which POSTs the
        # reject to the sub-agent's own server (see
        # TestPermissionGating.test_deny_with_reason_sends_correction for
        # the same mock). Without it, deny() tries to reach a real server
        # on port 14100, the request fails, and deny() swallows that
        # failure — leaving status stuck at "waiting_for_input" for a
        # reason that has nothing to do with the timeout logic under test.
        with (
            patch.object(mgr, "_get_client") as mock_client,
            patch.object(asyncio, "sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            mock_http = AsyncMock()
            mock_http.post.return_value = AsyncMock()
            mock_http.post.return_value.raise_for_status = MagicMock()
            mock_client.return_value = mock_http

            mock_sleep.side_effect = [None, asyncio.CancelledError()]
            task = asyncio.create_task(mgr._prune_loop())
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert state.status == "running"
        assert state.pending_permission is None


class TestShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_stops_all_agents(self):
        mgr = SubAgentManager()
        state = SubAgentState(
            session_id="s1", prompt="do", cwd=None, port=14100, status="running",
        )
        mgr._agents["s1"] = state

        await mgr.shutdown()
        assert len(mgr._agents) == 0

    @pytest.mark.asyncio
    async def test_shutdown_closes_client(self):
        mgr = SubAgentManager()
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()
        mgr._client = mock_client

        await mgr.shutdown()
        mock_client.aclose.assert_called_once()
        assert mgr._client is None


class TestStartFireAndForget:
    @pytest.mark.asyncio
    async def test_start_returns_immediately(self):
        mgr = SubAgentManager(_FakeSettings(agent_max_concurrent=5))

        with (
            patch.object(mgr, "_spawn_server_only", new_callable=AsyncMock) as mock_spawn,
            patch.object(mgr, "_bootstrap_and_listen", new_callable=AsyncMock) as mock_bootstrap,
            patch.object(asyncio, "create_task") as mock_create_task,
        ):
            state = await mgr.start("do stuff", cwd="/tmp")

            assert state.session_id.startswith("agent-")
            assert state.status == "starting"
            assert state.prompt == "do stuff"
            assert state.cwd == "/tmp"

            mock_spawn.assert_called_once()
            mock_create_task.assert_called_once()
            mock_bootstrap.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_fills_semaphore(self):
        mgr = SubAgentManager(_FakeSettings(agent_max_concurrent=1))
        initial = mgr._semaphore._value

        with (
            patch.object(mgr, "_spawn_server_only", new_callable=AsyncMock),
            patch.object(mgr, "_bootstrap_and_listen", new_callable=AsyncMock),
        ):
            await mgr.start("task 1")
            assert mgr._semaphore._value == initial - 1

            with pytest.raises(RuntimeError, match="Max 1 concurrent"):
                await mgr.start("task 2")
