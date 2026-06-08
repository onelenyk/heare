"""Pipecat-native pipeline assembly.

Pipeline shape (top-level):

    transport.input
      → stt
      → stt_error_observer
      → TranscriptionGateProcessor      (PH2-01)
      → user_aggregator                 (LLMContextAggregatorPair.user())
      → OpenRouterLLMService            (Pipecat-native LLM)
      → tts
      → AssistantResponseProcessor      (BOTLOG-02)
      → [optional sound_cue_processor]
      → transport.output
      → assistant_aggregator            (LLMContextAggregatorPair.assistant())

The LLMContext is built with the ToolsSchema from ``src.agent.tools.schemas``
(13 enabled tools). Tool execution flows through Pipecat's native
register_function handlers — the legacy two-stage intent-tag
machinery has been removed.

Pipecat imports are deferred so admin CLI paths import this module
without portaudio. ``build_pipeline`` is the production
entry point; ``_assemble_native_stages`` is the pure stage-list
builder, exposed for unit testing without portaudio mock state.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Tuple

from src.pipeline.stages.assistant_response_logger import create_assistant_response_logger
from src.pipeline.stages.tts_scrub_processor import create_tts_scrub_processor
from src.pipeline.stages.usage_recorder import create_usage_recorder
from src.pipeline.stages.cancel_flag_gate import create_cancel_flag_gate
from src.pipeline.stages.interrupt_toggle_gate import create_interrupt_toggle_gate
from src.pipeline.stages.mute_gate import create_input_mute_gate, create_mute_gate
from src.agent.llm.providers import PROVIDERS, get_available
from src.config import Settings
from src.agent.subagent_manager import SubAgentManager, set_agent_manager
from src.pipeline.bot_speech_state import BotSpeechState
from src.pipeline.language_state import LanguageState
from src.state import State
from src.agent.llm.context_injector import (
    create_system_prompt_injector,
    render_native_system_prompt,
)
from src.agent.tools.system import build_tools_schema, register_all_tools
from src.pipeline.stages.transcription_gate import create_transcription_gate
from src.pipeline.stages.agent_state_observer import create_agent_state_observer
from src.pipeline.stages.voice_state_observer import create_voice_state_observer
from src.pipeline.stages.echo_classifier import create_echo_classifier
from src.voice.tts.cache import TTSCache
from src.voice.tts.edge import create_edge_tts_service

if TYPE_CHECKING:
    from src.store.context import ContextBuilder
    from src.store.storage import TranscriptStore


logger = logging.getLogger("heare.pipeline_native")

# Module-level transport reference so ``heare audio-input`` /
# ``heare audio-output`` can change the device at runtime without
# restarting the daemon. Set by build_pipeline; read by reload_audio_device.
_transport_ref: dict[str, object] = {"transport": None}


def reload_audio_device(settings: "Settings") -> bool:
    """Reconfigure the audio output device at runtime.

    Reads the device name from ``settings.audio_output_device_file``,
    resolves it to a sounddevice index, and updates the running
    transport's output stream. Returns True on success.
    """
    transport = _transport_ref.get("transport")
    if transport is None:
        logger.warning("reload_audio_device: no transport reference")
        return False

    name = settings.audio_output_device
    if not name:
        logger.info("reload_audio_device: no device configured")
        return False

    try:
        import sounddevice as _sd

        devices = _sd.query_devices()
        idx = _resolve_device_index(name, devices, kind="output")
        if idx is None:
            logger.warning(
                "reload_audio_device: device %r not found", name
            )
            return False
    except Exception:
        logger.exception("reload_audio_device: device lookup failed")
        return False

    # Access the transport's internal params and reopen the stream.
    try:
        output = getattr(transport, "_output", None)
        if output is None:
            logger.warning("reload_audio_device: no output transport")
            return False

        # Stop the current stream.
        stream = getattr(output, "_out_stream", None)
        if stream is not None:
    from src.agent.tools.direct import set_memory_backend
    set_memory_backend(memory_backend)
    mcp_registered = mcp_bridge.register(
        llm_service, conversation_manager, session_state=session_state
    )
    if mcp_registered:
        logger.info(
            "mcp_bridge: registered handlers: %s",
            ", ".join(mcp_registered),
        )
    _wire_language_state(
        language_state, llm_context, persona,
        project_dir=project_dir,
        workspace_dir=str(settings.workspace_dir),
    )

    # Load dynamic tools from database and register them
    dynamic_tools = await store.load_all_dynamic_tools()
    for tool_dict in dynamic_tools:
        if not tool_dict.get("enabled", True):
            continue

        name = tool_dict["name"]
        try:
            definition = json.loads(tool_dict["definition_json"])

            # Register in tool_registry runtime cache
            from src.agent.tools.registry import register_dynamic_tool, Tool
            register_dynamic_tool(
                Tool(
                    name=name,
                    sdk_name=tool_dict["sdk_name"],
                    execution="direct",
                    description=tool_dict["description"],
                    enabled=True,
                )
            )

            # Register schema in llm_tools
            from src.agent.tools.schemas import register_dynamic_tool_schema
            register_dynamic_tool_schema(
                name=name,
                schema=definition.get("arguments", {}),
                impl_type=definition.get("implementation_type", "bash"),
                impl=definition.get("implementation", ""),
            )

            # Register handler with LLM service
            from src.agent.tools.schemas import register_dynamic_tool_handler
            register_dynamic_tool_handler(
                llm_service,
                name=name,
                impl_type=definition.get("implementation_type", "bash"),
                impl=definition.get("implementation", ""),
                settings=settings,
                conversation_manager=conversation_manager,
            )

            logger.info("Loaded dynamic tool: %s", name)
        except Exception as e:
            logger.warning("Failed to load dynamic tool %s: %s", name, e)

    # US-007: build the unified CapabilityIndex (skills + MCP + tools) so the
    # system prompt injector can surface top-K relevant capabilities per turn,
    # and the discover/install/revoke direct tools can resolve slugs.
    capability_index: Any = None
    try:
        from src.agent.tools.capability_index import build_capability_index
        from src.agent.tools.direct import set_capability_index

        capability_index = build_capability_index(settings, settings.workspace_dir)
        set_capability_index(capability_index)
    except Exception:
        logger.exception("pipeline_native: capability_index build failed (non-fatal)")

    # Multi-agent background sub-agent system
    agent_manager = SubAgentManager(settings)
    set_agent_manager(agent_manager)
    agent_manager.start_pruner()
    logger.info(
        "Sub-agent manager: ready (max=%d concurrent)",
        settings.agent_max_concurrent,
    )

    # PH2-07: per-turn dynamic system prompt — every TranscriptionFrame
    # passing the gate triggers the injector to rebuild the system
    # message with fresh persona+context+language before the
    # user_aggregator appends the user turn.
    system_prompt_injector = create_system_prompt_injector(
        llm_context=llm_context,
        context_builder=context_builder,
        persona=persona,
        language_state=language_state,
        conversation_manager=conversation_manager,
        capability_index=capability_index,
    )

    # Capture LLM text upstream of TTS and log per-response to transcripts.
    assistant_response_logger = create_assistant_response_logger(
        store=store,
        settings=settings,
        session_state=session_state,
        bot_speech_state=bot_speech_state,
    )

    # Strip tool-name narration before TTS speaks it. Without this, the LLM
    # occasionally emits raw tool names (``list_tools``, ``list_capabilities``,
    # ``bash: <command>``) as plain text instead of invoking the function — and
    # those words go straight to the user's speakers.
    tts_scrub = create_tts_scrub_processor()

    # Record token-usage events to ``usage_events`` so the dashboard
    # can show running cost. The recorder watches three Pipecat signals:
    # LLMUsageMetricsData, TTSUsageMetricsData, and finalized
    # TranscriptionFrame (with VAD-bracketed audio_seconds). Provider
    # getter reads the live ``llm_service.active_provider`` so a
    # mid-session switch via ``SwitchableLLMService`` flows through to
    # the ledger; STT/TTS providers are static here because pipecat's
    # metrics frames don't carry the slug pricing.py expects.
    usage_recorder = create_usage_recorder(
        store=store,
        provider_getter=lambda: getattr(llm_service, "active_provider", "unknown"),
        stt_provider="groq-whisper-large-v3",
        tts_provider="edge_tts",
    )

    # Mute gate — drops TTSAudioRawFrame when ``settings.mute_file`` exists.
    # Toggled from the watch dashboard (or any other process) by creating /
    # removing the file. Bot text is still logged because capture happens
    # upstream of TTS.
    mute_gate = create_mute_gate(
        state=state, session_state=session_state
    )

    # Input (mic) mute gate — drops InputAudioRawFrame when
    # ``settings.mute_input_file`` exists. Sits at the very front of the
    # pipeline so STT never even sees the muted audio.
    input_mute_gate = create_input_mute_gate(state=state)

    # Interrupt-toggle gate — flag-file-driven barge-in guard. When the
    # user disables interrupt (via the dashboard button → /interrupt API),
    # ``interrupt_enabled_file`` is created and this gate drops mic input
    # while the bot is speaking, letting the bot finish its utterance.
    interrupt_toggle_gate = create_interrupt_toggle_gate(settings=settings)

    # Cancel-flag gate — external "interrupt now" trigger. Mirrors the
    # mute-gate flag-file contract: any process (overlay, watch dashboard,
    # hotkey daemon) touches ``settings.cancel_flag_file`` and the next
    # pipeline frame pushes an InterruptionFrame upstream.
    cancel_flag_gate = create_cancel_flag_gate(
        state=state
    )

    # Acoustic echo gate — cross-correlates mic input against recent bot
    # output audio and drops correlated frames before they reach STT.
    echo_gate_proc = None
    echo_collector = None
    if settings.echo_gate_enabled:
        try:
            from src.pipeline.echo_state import EchoState
            from src.pipeline.stages.echo_gate import create_echo_gate_stages

            echo_state = EchoState(
                buffer_seconds=settings.echo_gate_buffer_seconds,
                target_sample_rate=16000,
            )
            echo_collector, echo_gate_proc = create_echo_gate_stages(
                echo_state, settings
            )
            logger.info(
                "echo_gate: active (threshold=%.2f, buffer=%.1fs, cooldown=%.1fs)",
                settings.echo_gate_threshold,
                settings.echo_gate_buffer_seconds,
                settings.echo_gate_cooldown_seconds,
            )
        except Exception:  # noqa: BLE001
            logger.exception("echo_gate: creation failed (non-fatal)")
            echo_gate_proc = None
            echo_collector = None

    # ------------------------------------------------------------------
    # Compose stages and build the task
    # ------------------------------------------------------------------
    stages = _assemble_native_stages(
        transport_input=transport.input(),
        transport_output=transport.output(),
        stt=stt,
        stt_error_observer=stt_error_observer,
        transcription_gate=transcription_gate,
        echo_classifier=echo_classifier,
        voice_state_observer=voice_state_observer,
        agent_state_observer=agent_state_observer,
        system_prompt_injector=system_prompt_injector,
        user_aggregator=user_aggregator,
        llm_service=llm_service,
        assistant_response_logger=assistant_response_logger,
        tts_scrub=tts_scrub,
        usage_recorder=usage_recorder,
        tts=tts,
        tts_fade_observer=tts_fade_observer,
        assistant_aggregator=assistant_aggregator,
        sound_cue_processor=sound_cue_processor,
        mute_gate=mute_gate,
        input_mute_gate=input_mute_gate,
        interrupt_toggle_gate=interrupt_toggle_gate,
        cancel_flag_gate=cancel_flag_gate,
        echo_gate=echo_gate_proc,
        echo_collector=echo_collector,
    )

    logger.info(
        "Pipecat-native pipeline assembled: provider=%s, model=%s, lang=%s, tools=%d",
        llm_service.active_provider,
        getattr(settings, f"{llm_service.active_provider}_model", PROVIDERS[llm_service.active_provider].default_model),
        language_state.language,
        len(tools_schema.standard_tools),
    )

    pipeline = Pipeline(stages)
    task = PipelineTask(
        pipeline,
        # Interruption is ON for deliberate cancel words ("stop"/"стоп"
        # /"відміни"/etc.) — TranscriptionGate detects them and pushes
        # InterruptionFrame upstream, which Pipecat routes through every
        # stage immediately, cancelling in-flight TTS, LLM generation,
        # and tool calls. The _TtsFadeOnInterruption observer additionally
        # fires a 50ms TTS fade-out as a polish layer.
        # VAD-triggered barge-in is still OFF: the bot's own audio leaking
        # back through the mic would otherwise preempt TTS and cause
        # choppy playback. The TranscriptionGate's barge-in + echo checks
        # gate genuine interruptions from VAD-triggered frames separately.
        # enable_metrics + enable_usage_metrics make the OpenAI-style LLM
        # service emit MetricsFrame[LLMUsageMetricsData] after each
        # completion — the UsageRecorder stage feeds those into
        # usage_events so the dashboard can show running cost.
        params=PipelineParams(
            allow_interruptions=True,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        cancel_on_idle_timeout=False,
        enable_turn_tracking=False,
    )
    return (
        task,
        transcription_gate,
        tts_cache,
        indication,
        llm_service,
        language_state,
        mcp_bridge,
        agent_manager,
    )


__all__ = [
    "build_pipeline",
    "_assemble_native_stages",
    "_build_system_prompt",
    "_wire_language_state",
]
