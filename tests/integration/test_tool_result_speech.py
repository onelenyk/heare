"""PH2-08 — End-to-end structural smoke test for native tool-call flow.

This test asserts the wiring that makes the user story
"ask 'check system status' → agent runs tool → agent SPEAKS the actual
result" possible, without driving a live LLM call (which would require
network + an OPENROUTER_API_KEY in CI).

Live verification of the spoken response (the manual smoke test in the
PRD AC list) is intentionally NOT automated: it requires portaudio,
microphone access, and a real LLM round-trip — none of which are
available in CI. The README/progress.txt documents the manual test.

What this test verifies (structural):
1. The 13 enabled tools are registered as Pipecat ``register_function``
   handlers when ``register_all_tools`` is called.
2. A bash tool_call dispatched through the registered handler reaches
   ``execute_direct`` and pushes a structured result via the supplied
   ``result_callback`` (no raw exceptions).
3. The result is a dict carrying actual data (a "summary" or success
   marker) — not just an acknowledgement string.
4. Pipeline stage assembly contains the LLM service between the user
   aggregator and the TTS — proving an LLM response naturally flows
   to TTS output without the legacy ActionWorker side-channel.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import Settings
from src.agent.tools.schemas import build_tools_schema, register_all_tools
from src.pipeline.build import _assemble_native_stages
from src.agent.tools.registry import get_enabled_tools


@pytest.fixture
def fake_llm():
    """Pipecat-LLM-shaped stub that records register_function calls."""

    class FakeLLM:
        def __init__(self) -> None:
            self.registered: dict[str, tuple] = {}

        def register_function(
            self,
            name: str,
            handler,
            *,
            cancel_on_interruption: bool = True,
        ) -> None:
            self.registered[name] = (handler, cancel_on_interruption)

    return FakeLLM()


def test_all_enabled_tools_are_registered(fake_llm) -> None:
    """AC1: tool_registry's 13 enabled tools become pipecat handlers."""
    register_all_tools(fake_llm, settings=Settings())
    enabled = set(get_enabled_tools())
    assert set(fake_llm.registered.keys()) == enabled


def test_tools_schema_matches_registered_set(fake_llm) -> None:
    """AC2: every registered handler has a FunctionSchema in ToolsSchema."""
    register_all_tools(fake_llm, settings=Settings())
    schema = build_tools_schema()
    schema_names = {fs.name for fs in schema.standard_tools}
    assert schema_names == set(fake_llm.registered.keys())


async def test_bash_handler_dispatches_to_execute_direct(
    fake_llm, monkeypatch
) -> None:
    """AC3: a bash tool_call reaches execute_direct and the result fires
    via result_callback (no raw exception raised into pipecat)."""
    captured: dict = {}

    async def fake_execute_direct(tool_name, args, settings):
        captured["tool"] = tool_name
        captured["args"] = args
        return {"success": True, "summary": "load average: 1.42"}

    monkeypatch.setattr("src.agent.tools.schemas.execute_direct", fake_execute_direct)

    register_all_tools(fake_llm, settings=Settings())
    handler, _cancel_flag = fake_llm.registered["bash"]

    delivered = AsyncMock()
    params = MagicMock()
    params.arguments = {"command": "uptime"}
    params.result_callback = delivered

    await handler(params)

    assert captured["tool"] == "bash"
    assert captured["args"] == "uptime"
    delivered.assert_awaited_once()
    payload = delivered.await_args.args[0]
    assert payload.get("success") is True
    assert "1.42" in str(payload)  # actual data, not just "Done"


def test_pipeline_routes_llm_through_tts_to_output() -> None:
    """AC4: the assembled stage list places LLM before TTS before
    transport.output. This is the contract that lets the model's spoken
    response (TTS frames generated from LLM tokens) reach the speaker
    without the legacy ActionWorker side-channel."""
    stages = _assemble_native_stages(
        transport_input="INPUT",
        transport_output="OUTPUT",
        stt="STT",
        stt_error_observer="STT_ERR",
        transcription_gate="GATE",
        user_aggregator="USER_AGG",
        llm_service="LLM",
        tts="TTS",
        assistant_aggregator="ASSIST_AGG",
    )
    assert stages.index("LLM") < stages.index("TTS")
    assert stages.index("TTS") < stages.index("OUTPUT")
    # No GeneratorProcessor / ActionWorker indirection — TTS comes
    # straight from the LLM stream.
    assert all(stage != "GENERATOR" for stage in stages)
    assert all(stage != "ACTION_WORKER" for stage in stages)


def test_no_intent_or_action_tag_grammar_in_native_stages() -> None:
    """AC5: zero live IntentQueue / ActionWorker / intent_parser
    surface in the post-cutover wiring path. Imports of legacy modules
    must be gone (the modules themselves were deleted)."""
    import src.pipeline.build as pipeline_mod
    import src.pipeline.stages.transcription_gate as gate_mod
    import src.main as main_mod

    forbidden_imports = (
        "from .actions",
        "from .intent_parser",
        "from .agent_sdk_cli",
        "from .openrouter_cli",
        "from .zai_cli",
        "from .openrouter_topic_extractor",
    )
    for mod in (pipeline_mod, gate_mod, main_mod):
        src_text = open(mod.__file__).read()
        for token in forbidden_imports:
            assert token not in src_text, (
                f"{mod.__name__} still imports legacy module via {token!r}"
            )
