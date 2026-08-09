"""Tests for llm_tools — Pipecat register_function bridge (PH2-03)."""
from __future__ import annotations

import types
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pipecat.adapters.schemas.function_schema")

from src.agent.tools.system import TOOLS as SYSTEM_TOOLS, build_tools_schema, register_all_tools  # noqa: E402


class _MockState:
    """Minimal State mock for testing."""
    def __init__(self, **initial):
        self._data = dict(initial)

    def get_bool(self, key: str) -> bool:
        return self._data.get(key) == "1"

    def get(self, key: str, default: str = "") -> str:
        return self._data.get(key, default)

    async def set(self, key: str, value: str):
        self._data[key] = value

    async def set_bool(self, key: str, value: bool):
        self._data[key] = "1" if value else "0"


class _FakeLLM:
    """Minimal stand-in for an LLMService that just records registrations."""

    def __init__(self) -> None:
        self.registered: dict[str, Any] = {}
        self.cancel_flags: dict[str, bool] = {}

    def register_function(
        self,
        function_name: str,
        handler: Any,
        *,
        cancel_on_interruption: bool = True,
        **_: Any,
    ) -> None:
        self.registered[function_name] = handler
        self.cancel_flags[function_name] = cancel_on_interruption


def test_build_tools_schema_covers_every_enabled_tool() -> None:
    schema = build_tools_schema()
    standard = schema.standard_tools
    names = {tool.name for tool in standard}
    enabled = {t.name for t in SYSTEM_TOOLS if t.enabled}
    assert names == enabled, (
        f"schema names {names!r} != enabled tools {enabled!r}"
    )


def test_function_schema_has_properties_and_required() -> None:
    schema = build_tools_schema()
    by_name = {t.name: t for t in schema.standard_tools}

    bash = by_name["bash"]
    assert "command" in bash.properties
    assert bash.required == ["command"]

    write = by_name["write"]
    assert {"path", "content"} <= set(write.properties.keys())
    assert set(write.required) == {"path", "content"}


def test_register_all_tools_registers_every_enabled_tool() -> None:
    llm = _FakeLLM()
    names = register_all_tools(llm, settings=None)
    enabled = {t.name for t in SYSTEM_TOOLS if t.enabled}
    assert set(names) == enabled
    assert set(llm.registered.keys()) == enabled


def test_register_all_tools_cancel_flag_for_cancel_is_false() -> None:
    """The `cancel` tool is interruption-routed in PH2-05 — we register
    it so the LLM has a parsable tool surface, but cancel_on_interruption
    must be False so Pipecat doesn't double-cancel a non-running call."""
    llm = _FakeLLM()
    register_all_tools(llm, settings=None)
    assert llm.cancel_flags["cancel"] is False
    # Sanity: regular tools opt INTO interruption-cancel.
    assert llm.cancel_flags["bash"] is True


@pytest.mark.asyncio
async def test_handler_dispatches_to_execute_direct(monkeypatch) -> None:
    """End-to-end registration AC: an LLM tool_call handler executes
    the underlying tool and fires result_callback."""
    captured: dict[str, Any] = {}

    async def fake_execute_bash(args: str, settings: Any) -> dict:
        captured["args"] = args
        captured["settings"] = settings
        return {"success": True, "output": "ok", "error": None}

    monkeypatch.setattr("src.agent.tools.direct._execute_bash", fake_execute_bash)

    llm = _FakeLLM()
    register_all_tools(llm, settings=None)

    bash_handler = llm.registered["bash"]
    rcb = AsyncMock()
    params = types.SimpleNamespace(
        arguments={"command": "uptime"},
        result_callback=rcb,
    )

    await bash_handler(params)

    assert captured["args"] == "uptime"
    rcb.assert_awaited_once()
    result = rcb.await_args.args[0]
    assert result == {"success": True, "output": "ok", "error": None}


@pytest.mark.asyncio
async def test_handler_serializes_complex_args_for_write(
    monkeypatch,
) -> None:
    """write expects 'filepath: content' on the legacy execute_direct
    surface; verify the structured args dict is folded down correctly."""
    captured: dict[str, Any] = {}

    async def fake_execute_write(args: str, settings: Any) -> dict:
        captured["args"] = args
        return {"success": True, "output": "wrote"}

    monkeypatch.setattr("src.agent.tools.direct._execute_write", fake_execute_write)

    llm = _FakeLLM()
    register_all_tools(llm, settings=None)

    handler = llm.registered["write"]
    rcb = AsyncMock()
    params = types.SimpleNamespace(
        arguments={"path": "/tmp/x", "content": "hello\nworld"},
        result_callback=rcb,
    )

    await handler(params)

    assert captured["args"] == "/tmp/x: hello\nworld"


@pytest.mark.asyncio
async def test_handler_swallows_exception_into_failure_result(
    monkeypatch,
) -> None:
    """Handler exception path must NOT raise into pipecat — it should
    surface a structured failure dict via result_callback."""

    async def boom(*args, **kwargs) -> dict:
        raise RuntimeError("dispatch failed")

    monkeypatch.setattr("src.agent.tools.direct._execute_bash", boom)

    llm = _FakeLLM()
    register_all_tools(llm, settings=None)

    handler = llm.registered["bash"]
    rcb = AsyncMock()
    params = types.SimpleNamespace(
        arguments={"command": "uptime"},
        result_callback=rcb,
    )

    await handler(params)  # must not raise

    rcb.assert_awaited_once()
    result = rcb.await_args.args[0]
    assert result["success"] is False
    assert "dispatch failed" in result.get("error", "")


@pytest.mark.asyncio
async def test_handler_cancellation_propagates(monkeypatch) -> None:
    """CancelledError must propagate so Pipecat's runner sees the
    cancel — our handler does NOT swallow it. The bash kill paths
    inside ``execute_direct`` (CCS-05b) own subprocess cleanup."""
    import asyncio

    async def cancelled(*args, **kwargs) -> dict:
        raise asyncio.CancelledError()

    monkeypatch.setattr("src.agent.tools.direct._execute_bash", cancelled)

    llm = _FakeLLM()
    register_all_tools(llm, settings=None)

    handler = llm.registered["bash"]
    rcb = AsyncMock()
    params = types.SimpleNamespace(
        arguments={"command": "sleep 60"},
        result_callback=rcb,
    )

    with pytest.raises(asyncio.CancelledError):
        await handler(params)

    rcb.assert_not_awaited()


@pytest.mark.asyncio
async def test_slow_tool_returns_a_readable_error_not_none(monkeypatch) -> None:
    """A tool that overruns its deadline must hand the model a real error.

    Pipecat's own timeout delivers ``result=None``, which the aggregator
    records as a bare "COMPLETED" and — the result being falsy — never
    re-runs the LLM, so the turn dies with no reply at all. We time the
    call out first and return something the model can actually say.
    """
    import asyncio

    from src.agent.tools import system

    monkeypatch.setitem(system._TOOL_TIMEOUTS, "bash", 0.05)

    inner_cancelled = asyncio.Event()

    async def never_finishes(*args, **kwargs) -> dict:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            inner_cancelled.set()
            raise
        return {"success": True, "output": "unreachable", "error": None}

    monkeypatch.setattr("src.agent.tools.direct._execute_bash", never_finishes)

    llm = _FakeLLM()
    register_all_tools(llm, settings=None)

    rcb = AsyncMock()
    params = types.SimpleNamespace(
        arguments={"command": "sleep 30"},
        result_callback=rcb,
    )

    await llm.registered["bash"](params)

    rcb.assert_awaited_once()
    result = rcb.await_args.args[0]
    assert result is not None
    assert result["success"] is False
    assert "did not" in result["error"] or "without finishing" in result["error"]
    # The tool coroutine must actually be torn down, so _execute_bash's
    # CancelledError branch can kill its process group.
    assert inner_cancelled.is_set()


def test_pipecat_timeout_is_registered_strictly_later_than_ours() -> None:
    """Pipecat's fallback must never fire before our own deadline.

    If it did, we would be back to a bare ``None`` result and a silent
    dead turn — the exact failure this margin exists to prevent.
    """
    from src.agent.tools import system

    class _RecordingLLM(_FakeLLM):
        def __init__(self) -> None:
            super().__init__()
            self.timeouts: dict[str, float] = {}

        def register_function(self, function_name, handler, **kw):  # type: ignore[override]
            super().register_function(function_name, handler, **kw)
            self.timeouts[function_name] = kw["timeout_secs"]

    llm = _RecordingLLM()
    register_all_tools(llm, settings=None)

    assert llm.timeouts, "no tools registered"
    for name, registered in llm.timeouts.items():
        assert registered > system.tool_timeout_secs(name), name


# ---------------------------------------------------------------------------
# Action-log wiring (architect HIGH fix): every tool invocation routes
# through ``record_action_pending`` / ``record_action_result`` /
# ``record_action_cancelled`` / ``record_action_error`` when a
# ``conversation_manager`` is supplied to ``register_all_tools``.


class _FakeConvMgr:
    def __init__(self) -> None:
        self.pending: list[tuple] = []
        self.results: list[tuple] = []
        self.cancels: list[tuple] = []
        self.errors: list[tuple] = []

    def record_action_pending(self, intent_id, tool, args) -> None:
        self.pending.append((intent_id, tool, args))

    def record_action_result(self, intent_id, summary, *, items=None) -> None:
        self.results.append((intent_id, summary, items))

    def record_action_cancelled(self, intent_id, tool="", args="") -> None:
        self.cancels.append((intent_id, tool, args))

    def record_action_error(self, intent_id, error) -> None:
        self.errors.append((intent_id, error))


@pytest.mark.asyncio
async def test_handler_records_pending_then_result(monkeypatch) -> None:
    async def fake_exec(args, settings):
        return {
            "success": True,
            "summary": "load average: 1.42",
            "items": [{"title": "h1"}],
        }

    monkeypatch.setattr("src.agent.tools.direct._execute_bash", fake_exec)

    cmgr = _FakeConvMgr()
    llm = _FakeLLM()
    register_all_tools(llm, settings=None, conversation_manager=cmgr)

    handler = llm.registered["bash"]
    rcb = AsyncMock()
    await handler(
        types.SimpleNamespace(
            arguments={"command": "uptime"}, result_callback=rcb
        )
    )

    assert len(cmgr.pending) == 1
    iid_p, tool_p, args_p = cmgr.pending[0]
    assert tool_p == "bash" and args_p == "uptime"
    assert len(cmgr.results) == 1
    iid_r, summary, items = cmgr.results[0]
    assert iid_p == iid_r
    assert summary == "load average: 1.42"
    assert items == [{"title": "h1"}]


@pytest.mark.asyncio
async def test_handler_records_error_when_execute_raises(monkeypatch) -> None:
    async def boom(*args, **kwargs):
        raise RuntimeError("dispatch failed")

    monkeypatch.setattr("src.agent.tools.direct._execute_bash", boom)

    cmgr = _FakeConvMgr()
    llm = _FakeLLM()
    register_all_tools(llm, settings=None, conversation_manager=cmgr)

    handler = llm.registered["bash"]
    rcb = AsyncMock()
    await handler(
        types.SimpleNamespace(
            arguments={"command": "uptime"}, result_callback=rcb
        )
    )

    assert len(cmgr.pending) == 1
    assert len(cmgr.errors) == 1
    assert "dispatch failed" in cmgr.errors[0][1]


@pytest.mark.asyncio
async def test_handler_records_cancelled_when_execute_cancels(monkeypatch) -> None:
    import asyncio

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr("src.agent.tools.direct._execute_bash", cancelled)

    cmgr = _FakeConvMgr()
    llm = _FakeLLM()
    register_all_tools(llm, settings=None, conversation_manager=cmgr)

    handler = llm.registered["bash"]
    rcb = AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        await handler(
            types.SimpleNamespace(
                arguments={"command": "sleep 60"}, result_callback=rcb
            )
        )

    assert len(cmgr.pending) == 1
    assert len(cmgr.cancels) == 1
    assert cmgr.cancels[0][1] == "bash"


@pytest.mark.asyncio
async def test_handler_without_conversation_manager_is_noop(monkeypatch) -> None:
    """Backwards-compat: existing call sites that omit conversation_manager
    keep their original behaviour (no-op on the action log)."""
    async def fake_exec(args, settings):
        return {"success": True, "summary": "ok"}

    monkeypatch.setattr("src.agent.tools.direct._execute_bash", fake_exec)

    llm = _FakeLLM()
    register_all_tools(llm, settings=None)  # no conversation_manager

    handler = llm.registered["bash"]
    rcb = AsyncMock()
    await handler(
        types.SimpleNamespace(
            arguments={"command": "uptime"}, result_callback=rcb
        )
    )
    rcb.assert_awaited_once()


# ---------------------------------------------------------------------------
# Integration tests for SwitchableLLMService + register_all_tools
# (US-004 / zai-anthropic-full-support I1-I3).
# Verifies tool fan-out, set_provider direct-tool wiring, and
# provider-agnostic schema construction.
# ---------------------------------------------------------------------------


@pytest.fixture
def _switchable_service(tmp_path):
    """Build a real SwitchableLLMService backed by both delegates."""
    pytest.importorskip("pipecat.services.openai.llm")
    pytest.importorskip("pipecat.services.anthropic.llm")

    from src.agent.llm.switchable import SwitchableLLMService

    return SwitchableLLMService(
        deepseek_api_key="sk-ds-test",
        deepseek_model="mock-ds",
        deepseek_base_url="https://api.deepseek.com/v1",
        zai_api_key="sk-zai-test",
        zai_model="claude-3-5-sonnet",
        zai_base_url="https://api.z.ai/api/anthropic",
        opencode_api_key=None,
        opencode_base_url="https://opencode.ai/zen/go/v1",
        opencode_model="minimax-m2.7",
        state=_MockState(),
    )


def test_register_all_tools_visible_on_both_delegates(_switchable_service) -> None:
    """I1: register_all_tools() fans out so both delegates see every tool."""
    swit = _switchable_service
    names = register_all_tools(swit, settings=None)

    enabled = {t.name for t in SYSTEM_TOOLS if t.enabled}
    assert set(names) == enabled

    or_funcs = set(swit._deepseek_service._functions.keys())
    zai_funcs = set(swit._zai_service._functions.keys())
    # Pipecat may include a None entry for "default handlers"; ignore it.
    or_named = {n for n in or_funcs if n is not None}
    zai_named = {n for n in zai_funcs if n is not None}
    assert enabled <= or_named, f"missing on DS delegate: {enabled - or_named}"
    assert enabled <= zai_named, f"missing on ZAI delegate: {enabled - zai_named}"


@pytest.mark.asyncio
async def test_set_provider_tool_writes_file_and_takes_effect(
    tmp_path, _switchable_service
) -> None:
    """I2: the set_provider direct tool sets the provider in state and the
    SwitchableLLMService picks it up on next sync."""
    from src.config import Settings
    from src.agent.tools.direct import _execute_set_provider

    swit = _switchable_service

    # Set provider in state, then trigger sync
    settings = Settings(provider_file=tmp_path / "provider")
    swit._state._data["provider"] = "zai"
    swit._sync_provider()

    assert swit.active_provider == "zai"


def test_tools_schema_is_provider_agnostic() -> None:
    """I3: build_tools_schema() yields a single ToolsSchema usable by both
    the OpenAI and Anthropic adapters; tool counts must match the system TOOLS."""
    schema = build_tools_schema()
    enabled = {t.name for t in SYSTEM_TOOLS if t.enabled}
    assert {t.name for t in schema.standard_tools} == enabled

    # The Pipecat adapters translate the schema per-provider. We assert that
    # both adapter classes accept the same ToolsSchema without error.
    pytest.importorskip("pipecat.adapters.services.open_ai_adapter")
    pytest.importorskip("pipecat.adapters.services.anthropic_adapter")

    from pipecat.adapters.services.open_ai_adapter import OpenAILLMAdapter
    from pipecat.adapters.services.anthropic_adapter import AnthropicLLMAdapter

    or_tools = OpenAILLMAdapter().to_provider_tools_format(schema)
    zai_tools = AnthropicLLMAdapter().to_provider_tools_format(schema)
    # Each adapter produces exactly one entry per enabled tool.
    assert len(or_tools) == len(enabled)
    assert len(zai_tools) == len(enabled)
