"""Pipecat-native pipeline assembly.

Pipeline shape (top-level):

    transport.input
      → [optional speaker_buffer]
      → stt
      → stt_error_observer
      → [optional speaker_tagger]
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
from src.pipeline.stages.mute_gate import create_input_mute_gate, create_mute_gate
from src.config import Settings
from src.pipeline.language_state import LanguageState
from src.agent.llm.context_injector import (
    create_system_prompt_injector,
    render_native_system_prompt,
)
from src.agent.tools.schemas import build_tools_schema, register_all_tools
from src.pipeline.stages.transcription_gate import create_transcription_gate
from src.voice.tts.cache import TTSCache
from src.voice.tts.edge import create_edge_tts_service

if TYPE_CHECKING:
    from src.store.context import ContextBuilder
    from src.store.storage import TranscriptStore


logger = logging.getLogger("heare.pipeline_native")


def _build_system_prompt(persona: str, language: str) -> str:
    """Construction-time system message — minimal, no conversation context.

    The pipeline-native graph also wires a ``SystemPromptInjector``
    (PH2-07) that rebuilds this prompt with the full ``ContextBuilder``
    output (recent transcripts, conversation memory, action log, MCP
    descriptions) for every user turn. This function is only used to
    seed the LLMContext at construction time before any utterance has
    arrived. Tests cover both the seed shape and the per-turn rebuild
    path independently.
    """
    return render_native_system_prompt(
        persona=persona, context=None, language=language
    )


def _wire_language_state(
    state: LanguageState,
    llm_context: Any,
    persona: str,
) -> None:
    """Update the LLMContext's first system message whenever the
    LanguageState changes. The user_aggregator reads the same context
    object, so the next LLM turn picks up the new system prompt without
    any explicit ``LLMUpdateSettingsFrame`` push.
    """

    def _on_language_change(new_lang: str) -> None:
        try:
            messages = llm_context.get_messages()
        except Exception:
            messages = getattr(llm_context, "_messages", None)
            if messages is None:
                logger.warning(
                    "pipeline_native: language listener could not access "
                    "LLMContext messages; skipping update"
                )
                return
        new_system = _build_system_prompt(persona, new_lang)
        for i, msg in enumerate(messages):
            if isinstance(msg, dict) and msg.get("role") == "system":
                messages[i] = {"role": "system", "content": new_system}
                logger.info(
                    "[LLM SYSTEM PROMPT REWRITE] lang=%s", new_lang
                )
                return
        # No system message yet — prepend.
        messages.insert(
            0, {"role": "system", "content": new_system}
        )
        logger.info(
            "[LLM SYSTEM PROMPT INSERT] lang=%s (no prior system message)",
            new_lang,
        )

    state.set_change_listener(_on_language_change)


def _assemble_native_stages(
    *,
    transport_input: Any,
    transport_output: Any,
    stt: Any,
    stt_error_observer: Any,
    transcription_gate: Any,
    user_aggregator: Any,
    llm_service: Any,
    tts: Any,
    assistant_response_logger: Any = None,
    tts_scrub: Any = None,
    usage_recorder: Any = None,
    tts_fade_observer: Any = None,
    assistant_aggregator: Any,
    system_prompt_injector: Any = None,
    speaker_buffer: Any = None,
    speaker_tagger: Any = None,
    sound_cue_processor: Any = None,
    mute_gate: Any = None,
    input_mute_gate: Any = None,
) -> list:
    """Pure stage-list assembly, factored out for unit testing.

    When supplied, ``system_prompt_injector`` (PH2-07) sits between
    the ``transcription_gate`` and the ``user_aggregator`` so it can
    rebuild the LLMContext's system message before the LLM sees the
    user turn. ``assistant_response_logger`` (BOTLOG-02) sits after
    the TTS service to intercept TTSAudioRawFrame and log only bot
    responses that are actually spoken (audio playing). ``tts_fade_observer``
    (PH2-05) sits immediately after the TTS service so any ``InterruptionFrame``
    propagating in either direction triggers the 50ms fade-out hook on the TTS service.
    """
    stages: list = [transport_input]
    # input_mute_gate sits as early as possible so muted mic audio is dropped
    # before STT (and any speaker buffering) runs.
    if input_mute_gate is not None:
        stages.append(input_mute_gate)
    if speaker_buffer is not None:
        stages.append(speaker_buffer)
    stages.extend([stt, stt_error_observer])
    if speaker_tagger is not None:
        stages.append(speaker_tagger)
    stages.append(transcription_gate)
    if system_prompt_injector is not None:
        stages.append(system_prompt_injector)
    stages.extend([user_aggregator, llm_service])
    # assistant_response_logger sits BETWEEN llm_service and tts so it can
    # observe LLMFullResponseStartFrame / LLMTextFrame / LLMFullResponseEndFrame
    # before TTS consumes them — EdgeTTSService never emits TTSTextFrame, so
    # capture must happen on the LLM side.
    if assistant_response_logger is not None:
        stages.append(assistant_response_logger)
    # tts_scrub sits AFTER assistant_response_logger so the logger
    # records the model's intended (raw) text for debugging, but BEFORE
    # tts so the user never hears tool-name-only utterances like
    # ``list_tools`` that the model emitted as text instead of invoking.
    if tts_scrub is not None:
        stages.append(tts_scrub)
    stages.append(tts)
    # usage_recorder sits AFTER tts so it observes every metrics frame
    # in the pipeline — LLMUsageMetricsData (from llm_service),
    # TTSUsageMetricsData (from tts), and the VAD bracket frames +
    # TranscriptionFrame that bound STT calls. Observe-only: forwards
    # every frame unchanged.
    if usage_recorder is not None:
        stages.append(usage_recorder)
    if tts_fade_observer is not None:
        stages.append(tts_fade_observer)
    if sound_cue_processor is not None:
        stages.append(sound_cue_processor)
    if mute_gate is not None:
        stages.append(mute_gate)
    stages.extend([transport_output, assistant_aggregator])
    return stages


async def build_pipeline(
    settings: Settings,
    store: "TranscriptStore",
    context_builder: "ContextBuilder",
    persona: str = "",
    *,
    conversation_manager: Any = None,
    speaker_gallery: Any = None,
    speaker_model: Any = None,
    namer_enqueue: Any = None,
) -> Tuple[object, object, object, object, object, object]:
    """Build the Pipecat-native pipeline.

    Returns
    -------
    (task, transcription_gate, tts_cache, indication, llm_service, language_state)

    The ``transcription_gate`` replaces the legacy ``processor`` slot
    in the return tuple — main.py will be updated alongside PH2-06.
    """
    # ------------------------------------------------------------------
    # Pipecat imports (deferred for admin-CLI compatibility)
    # ------------------------------------------------------------------
    from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
    from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import (
        LocalSmartTurnAnalyzerV3,
    )
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.frames.frames import ErrorFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
    )
    from pipecat.processors.frame_processor import (
        FrameProcessor as _FP,
    )
    from pipecat.services.groq.stt import GroqSTTService
    from pipecat.transcriptions.language import Language

    from src.agent.llm.switchable import SwitchableLLMService
    from pipecat.transports.local.audio import (
        LocalAudioTransport,
        LocalAudioTransportParams,
    )

    if not settings.groq_api_key:
        raise RuntimeError(
            "GROQ_API_KEY is not set — copy .env.example to .env and fill it in"
        )
    if not settings.openrouter_api_key and not settings.zai_api_key:
        raise RuntimeError(
            "Neither OPENROUTER_API_KEY nor ZAI_API_KEY is set — "
            "at least one is required for the LLM service"
        )

    # ------------------------------------------------------------------
    # Audio + STT + TTS (mostly identical to legacy build_pipeline)
    # ------------------------------------------------------------------
    vad = SileroVADAnalyzer(
        params=VADParams(
            stop_secs=0.5, start_secs=0.3, confidence=0.7, min_volume=0.6
        )
    )
    smart_turn = LocalSmartTurnAnalyzerV3(params=SmartTurnParams(stop_secs=1.0))
    transport = LocalAudioTransport(
        params=LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=settings.tts_sample_rate,
            vad_analyzer=vad,
            turn_analyzer=smart_turn,
        )
    )
    # STT language is a HINT for Groq's Whisper, not a hard force. Groq will detect
    # the language from audio and can override this hint if confident (e.g., English
    # speech despite "uk" hint). The TranscriptionGateProcessor reads Groq's detected
    # language and dynamically updates TTS voice to match.
    stt = GroqSTTService(
        api_key=settings.groq_api_key,
        language=Language(settings.groq_language),
        include_prob_metrics=True,
    )
    tts_cache = TTSCache()
    tts = create_edge_tts_service(
        voice=settings.tts_voice,
        sample_rate=settings.tts_sample_rate,
        cache=tts_cache,
    )

    # ------------------------------------------------------------------
    # Speaker chain (optional — same gating as legacy)
    # ------------------------------------------------------------------
    speaker_buffer = None
    speaker_tagger = None
    if (
        settings.speaker_id_enabled
        and speaker_gallery is not None
        and speaker_model is not None
    ):
        from src.voice.speaker.processor import create_speaker_processors

        speaker_buffer, speaker_tagger = create_speaker_processors(
            settings,
            speaker_gallery,
            speaker_model,
            namer_enqueue=namer_enqueue,
        )
        logger.info(
            "Speaker chain active: tagger wired; namer_enqueue=%s",
            "on" if namer_enqueue is not None else "off",
        )

    # ------------------------------------------------------------------
    # Indication subsystem (identical to legacy)
    # ------------------------------------------------------------------
    from src.voice.indication.core import Indication, build_sound_cue_processor, set_indication

    sound_cue_processor = None
    backends: list[Any] = []
    if settings.indication.enabled:
        if settings.indication.sound_enabled:
            from src.voice.indication.backends.sound import SoundBackend

            sound_cue_processor = build_sound_cue_processor(
                sample_rate=settings.tts_sample_rate
            )
            backends.append(
                SoundBackend(
                    sound_cue_processor, sample_rate=settings.tts_sample_rate
                )
            )
        if settings.indication.visual_enabled:
            from src.voice.indication.backends.visual import VisualBackend

            backends.append(
                VisualBackend(settings.log_dir / "indication.jsonl")
            )
        if settings.indication.notification_center_enabled:
            from src.voice.indication.backends.notification import NotificationBackend

            backends.append(NotificationBackend())
    indication = Indication(
        settings.indication, backends, mode_provider=lambda: settings.mode
    )
    set_indication(indication)
    logger.info(
        "indication: %d backend(s) ready (enabled=%s)",
        len(backends),
        settings.indication.enabled,
    )

    # ------------------------------------------------------------------
    # STT error observer (identical to legacy)
    # ------------------------------------------------------------------
    class _SttErrorObserver(_FP):  # type: ignore[misc,valid-type]
        async def process_frame(self, frame, direction) -> None:  # type: ignore[override]
            await super().process_frame(frame, direction)
            if isinstance(frame, ErrorFrame):
                from src.voice.indication.core import IndicationKind, get_indication

                ind_inner = get_indication()
                if ind_inner is not None:
                    err_msg = getattr(frame, "error", str(frame))
                    ind_inner.notify(
                        IndicationKind.STT_ERROR, body=str(err_msg)[:160]
                    )
            await self.push_frame(frame, direction)

    stt_error_observer = _SttErrorObserver()

    # ------------------------------------------------------------------
    # TTS fade-out observer (PH2-05): on InterruptionFrame, fire the
    # 50ms TTS fade hook from CCS-05b so any in-flight TTS frame stops
    # cleanly instead of clipping. Pipecat's native interruption already
    # cancels the in-flight register_function calls (cancel_on_interruption),
    # which trips execute_direct's CancelledError path → os.killpg for
    # bash subprocesses. The fade is a polish layer on top.
    # ------------------------------------------------------------------
    from pipecat.frames.frames import InterruptionFrame

    class _TtsFadeOnInterruption(_FP):  # type: ignore[misc,valid-type]
        def __init__(self, tts_service: Any) -> None:
            super().__init__()
            self._tts = tts_service

        async def process_frame(self, frame, direction) -> None:  # type: ignore[override]
            await super().process_frame(frame, direction)
            if isinstance(frame, InterruptionFrame):
                cancel_pending = getattr(self._tts, "cancel_pending", None)
                if callable(cancel_pending):
                    try:
                        result = cancel_pending()
                        if hasattr(result, "__await__"):
                            await result
                    except Exception:
                        logger.exception(
                            "pipeline_native: tts.cancel_pending raised "
                            "(non-fatal)"
                        )
            await self.push_frame(frame, direction)

    tts_fade_observer = _TtsFadeOnInterruption(tts)

    # ------------------------------------------------------------------
    # Phase-2 native components
    # ------------------------------------------------------------------
    language_state = LanguageState(
        initial=settings.groq_language
        if settings.groq_language not in ("auto", "")
        else "en"
    )
    transcription_gate = create_transcription_gate(
        store=store,
        settings=settings,
        tts_service=tts,
        language_state=language_state,
    )

    llm_service = SwitchableLLMService(
        openrouter_api_key=settings.openrouter_api_key,
        openrouter_model=settings.openrouter_model,
        zai_api_key=settings.zai_api_key,
        zai_model=settings.zai_model,
        zai_base_url=settings.zai_base_url,
        provider_file=settings.provider_file,
    )
    tools_schema = build_tools_schema()
    llm_context = LLMContext(
        messages=[
            {
                "role": "system",
                "content": _build_system_prompt(persona, language_state.language),
            }
        ],
        tools=tools_schema,
    )
    aggregator_pair = LLMContextAggregatorPair(llm_context)
    user_aggregator = aggregator_pair.user()
    assistant_aggregator = aggregator_pair.assistant()

    register_all_tools(
        llm_service,
        settings=settings,
        conversation_manager=conversation_manager,
    )
    _wire_language_state(language_state, llm_context, persona)

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
        store=store, settings=settings
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
    mute_gate = create_mute_gate(flag_path=settings.mute_file)

    # Input (mic) mute gate — drops InputAudioRawFrame when
    # ``settings.mute_input_file`` exists. Sits at the very front of the
    # pipeline so STT never even sees the muted audio.
    input_mute_gate = create_input_mute_gate(flag_path=settings.mute_input_file)

    # ------------------------------------------------------------------
    # Compose stages and build the task
    # ------------------------------------------------------------------
    stages = _assemble_native_stages(
        transport_input=transport.input(),
        transport_output=transport.output(),
        stt=stt,
        stt_error_observer=stt_error_observer,
        transcription_gate=transcription_gate,
        system_prompt_injector=system_prompt_injector,
        user_aggregator=user_aggregator,
        llm_service=llm_service,
        assistant_response_logger=assistant_response_logger,
        tts_scrub=tts_scrub,
        usage_recorder=usage_recorder,
        tts=tts,
        tts_fade_observer=tts_fade_observer,
        assistant_aggregator=assistant_aggregator,
        speaker_buffer=speaker_buffer,
        speaker_tagger=speaker_tagger,
        sound_cue_processor=sound_cue_processor,
        mute_gate=mute_gate,
        input_mute_gate=input_mute_gate,
    )

    logger.info(
        "Pipecat-native pipeline assembled: provider=%s, model=%s, lang=%s, tools=%d",
        llm_service.active_provider,
        settings.openrouter_model if llm_service.active_provider == "openrouter" else settings.zai_model,
        language_state.language,
        len(tools_schema.standard_tools),
    )

    pipeline = Pipeline(stages)
    task = PipelineTask(
        pipeline,
        # Native barge-in is OFF: any VAD trigger (including the bot's
        # own audio leaking back through the mic) would preempt the
        # current TTS turn and cause choppy playback. Explicit cancel
        # words ("stop"/"відміни"/etc.) still work — TranscriptionGate
        # detects them and pushes InterruptionFrame upstream, which the
        # _TtsFadeOnInterruption observer routes to the TTS fade-out.
        # enable_metrics + enable_usage_metrics make the OpenAI-style LLM
        # service emit MetricsFrame[LLMUsageMetricsData] after each
        # completion — the UsageRecorder stage feeds those into
        # usage_events so the dashboard can show running cost.
        params=PipelineParams(
            allow_interruptions=False,
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
    )


__all__ = [
    "build_pipeline",
    "_assemble_native_stages",
    "_build_system_prompt",
    "_wire_language_state",
]
