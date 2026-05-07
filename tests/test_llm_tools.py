"""Tests for llm_tools — Pipecat register_function bridge (PH2-03)."""
from __future__ import annotations

import types
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pipecat.adapters.schemas.function_schema")

from src.agent.tools.schemas import build_tools_schema, register_all_tools  # noqa: E402
from src.agent.tools.registry import get_enabled_tools  # noqa: E402


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
    assert names == get_enabled_tools(), (
        f"schema names {names!r} != enabled tools {get_enabled_tools()!r}"
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

    list_profiles = by_name["list_profiles"]
    assert list_profiles.required == []
    assert list_profiles.properties == {}


def test_register_all_tools_registers_every_enabled_tool() -> None:
    llm = _FakeLLM()
    names = register_all_tools(llm, settings=None)
    assert set(names) == get_enabled_tools()
    assert set(llm.registered.keys()) == get_enabled_tools()


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

    async def fake_execute_direct(tool: str, args: str, settings: Any) -> dict:
        captured["tool"] = tool
        captured["args"] = args
        captured["settings"] = settings
        return {"success": True, "output": "ok", "error": None}

    monkeypatch.setattr("src.agent.tools.schemas.execute_direct", fake_execute_direct)

    llm = _FakeLLM()
    register_all_tools(llm, settings=None)

    bash_handler = llm.registered["bash"]
    rcb = AsyncMock()
    params = types.SimpleNamespace(
        arguments={"command": "uptime"},
        result_callback=rcb,
    )

    await bash_handler(params)

    assert captured["tool"] == "bash"
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

    async def fake_execute_direct(tool: str, args: str, settings: Any) -> dict:
        captured["tool"] = tool
        captured["args"] = args
        return {"success": True, "output": "wrote"}

    monkeypatch.setattr("src.agent.tools.schemas.execute_direct", fake_execute_direct)

    llm = _FakeLLM()
    register_all_tools(llm, settings=None)

    handler = llm.registered["write"]
    rcb = AsyncMock()
    params = types.SimpleNamespace(
        arguments={"path": "/tmp/x", "content": "hello\nworld"},
        result_callback=rcb,
    )

    await handler(params)

    assert captured["tool"] == "write"
    assert captured["args"] == "/tmp/x: hello\nworld"


@pytest.mark.asyncio
async def test_handler_swallows_exception_into_failure_result(
    monkeypatch,
) -> None:
    """Handler exception path must NOT raise into pipecat — it should
    surface a structured failure dict via result_callback."""

    async def boom(*args, **kwargs) -> dict:
        raise RuntimeError("dispatch failed")

    monkeypatch.setattr("src.agent.tools.schemas.execute_direct", boom)

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

    monkeypatch.setattr("src.agent.tools.schemas.execute_direct", cancelled)

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
    async def fake_exec(name, args, settings):
        return {
            "success": True,
            "summary": "load average: 1.42",
            "items": [{"title": "h1"}],
        }

    monkeypatch.setattr("src.agent.tools.schemas.execute_direct", fake_exec)

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

    monkeypatch.setattr("src.agent.tools.schemas.execute_direct", boom)

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

    monkeypatch.setattr("src.agent.tools.schemas.execute_direct", cancelled)

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
    async def fake_exec(name, args, settings):
        return {"success": True, "summary": "ok"}

    monkeypatch.setattr("src.agent.tools.schemas.execute_direct", fake_exec)

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
        openrouter_api_key="sk-or-test",
        openrouter_model="mock-or",
        zai_api_key="sk-zai-test",
        zai_model="claude-3-5-sonnet",
        zai_base_url="https://api.z.ai/api/anthropic",
        provider_file=tmp_path / "provider",
    )


def test_register_all_tools_visible_on_both_delegates(_switchable_service) -> None:
    """I1: register_all_tools() fans out so both delegates see every tool."""
    swit = _switchable_service
    names = register_all_tools(swit, settings=None)

    enabled = get_enabled_tools()
    assert set(names) == enabled

    or_funcs = set(swit._or_service._functions.keys())
    zai_funcs = set(swit._zai_service._functions.keys())
    # Pipecat may include a None entry for "default handlers"; ignore it.
    or_named = {n for n in or_funcs if n is not None}
    zai_named = {n for n in zai_funcs if n is not None}
    assert enabled <= or_named, f"missing on OR delegate: {enabled - or_named}"
    assert enabled <= zai_named, f"missing on ZAI delegate: {enabled - zai_named}"


@pytest.mark.asyncio
async def test_set_provider_tool_writes_file_and_takes_effect(
    tmp_path, _switchable_service
) -> None:
    """I2: the set_provider direct tool writes the provider file and the
    SwitchableLLMService picks it up on next sync."""
    from src.config import Settings
    from src.agent.tools.direct import _execute_set_provider

    swit = _switchable_service

    # Repoint settings.provider_file at the same file the service watches.
    settings = Settings(provider_file=swit._provider_file)

    result = await _execute_set_provider("zai", settings)
    assert result["success"] is True
    assert swit._provider_file.read_text().strip() == "zai"

    # Next sync must flip the active provider.
    assert swit.active_provider == "zai"


def test_tools_schema_is_provider_agnostic() -> None:
    """I3: build_tools_schema() yields a single ToolsSchema usable by both
    the OpenAI and Anthropic adapters; tool counts must match the registry."""
    schema = build_tools_schema()
    enabled = get_enabled_tools()
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
