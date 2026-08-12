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
import time
from typing import TYPE_CHECKING, Any, Tuple

from src.pipeline.stages.assistant_response_logger import (
    create_assistant_response_logger,
)
from src.pipeline.stages.tts_scrub_processor import create_tts_scrub_processor
from src.pipeline.stages.usage_recorder import create_usage_recorder
from src.pipeline.stages.cancel_flag_gate import create_cancel_flag_gate
from src.pipeline.stages.interrupt_toggle_gate import create_interrupt_toggle_gate
from src.pipeline.stages.gain_control import (
    create_input_gain_processor,
    create_output_volume_processor,
)
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
from src.pipeline.wake import wake_phrases
from src.pipeline.stages.agent_state_observer import create_agent_state_observer
from src.pipeline.stages.voice_state_observer import create_voice_state_observer
from src.pipeline.stages.audio_monitor import SidetoneProcessor
from src.pipeline.stages.echo_classifier import create_echo_classifier
from src.voice.tts.cache import TTSCache
from src.voice.tts.edge import create_edge_tts_service

if TYPE_CHECKING:
    from src.store.context import ContextBuilder
    from src.store.storage import TranscriptStore
    from src.store.conversation import ConversationManager
    from src.memory.base import MemoryBackend


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
            logger.warning("reload_audio_device: device %r not found", name)
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
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                logger.exception("reload_audio_device: stream close failed")
            output._out_stream = None  # type: ignore[attr-defined]

        output._params.output_device_index = idx  # type: ignore[attr-defined]
        # start() recreates the stream with the new device index.
        from pipecat.frames.frames import StartFrame
        from src.async_utils import safe_task

        safe_task(output.start(StartFrame()), name="audio-output-reload")
        logger.info(
            "reload_audio_device: output switched to device %d (%s)",
            idx,
            name,
        )
        return True
    except Exception:
        logger.exception("reload_audio_device: reconfiguration failed")
        return False


def reload_audio_input_device(settings: "Settings") -> bool:
    """Reconfigure the audio input device at runtime.

    Mirrors ``reload_audio_device`` but targets the input stream.
    Returns True on success.
    """
    transport = _transport_ref.get("transport")
    if transport is None:
        logger.warning("reload_audio_input_device: no transport reference")
        return False

    name = settings.audio_input_device
    if not name:
        logger.info("reload_audio_input_device: no device configured")
        return False

    try:
        import sounddevice as _sd

        devices = _sd.query_devices()
        idx = _resolve_device_index(name, devices, kind="input")
        if idx is None:
            logger.warning("reload_audio_input_device: device %r not found", name)
            return False
    except Exception:
        logger.exception("reload_audio_input_device: device lookup failed")
        return False

    try:
        inp = getattr(transport, "_input", None)
        if inp is None:
            logger.warning("reload_audio_input_device: no input transport")
            return False

        stream = getattr(inp, "_in_stream", None)
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                logger.exception("reload_audio_input_device: stream close failed")
            inp._in_stream = None  # type: ignore[attr-defined]

        inp._params.input_device_index = idx  # type: ignore[attr-defined]
        from pipecat.frames.frames import StartFrame
        from src.async_utils import safe_task

        safe_task(inp.start(StartFrame()), name="audio-input-reload")
        logger.info(
            "reload_audio_input_device: input switched to device %d (%s)",
            idx,
            name,
        )
        return True
    except Exception:
        logger.exception("reload_audio_input_device: reconfiguration failed")
        return False


def _resolve_device_index(name: str, devices: list, kind: str) -> int | None:
    """Find a sounddevice index by substring match on device name.

    ``kind`` is ``"input"`` (device must have input channels) or
    ``"output"`` (must have output channels). Returns the first match
    or ``None``.
    """
    name_lower = name.lower()
    for i, dev in enumerate(devices):
        if name_lower in dev["name"].lower():
            has_input = int(dev.get("max_input_channels", 0)) > 0
            has_output = int(dev.get("max_output_channels", 0)) > 0
            if kind == "input" and has_input:
                return i
            if kind == "output" and has_output:
                return i
    return None


def _build_system_prompt(
    persona: str,
    language: str,
    project_dir: str | None = None,
    workspace_dir: str | None = None,
) -> str:
    """Construction-time system message — minimal, no conversation context.

    The pipeline-native graph also wires a ``SystemPromptInjector``
    (PH2-07) that rebuilds this prompt with the full ``ContextBuilder``
    output (recent transcripts, conversation memory, action log, MCP
    descriptions) for every user turn. This function is only used to
    seed the LLMContext at construction time before any utterance has
    arrived. Tests cover both the seed shape and the per-turn rebuild
    path independently.

    ``project_dir`` and ``workspace_dir`` are passed so the agent knows
    its code root and file-operation sandbox even at construction time
    (and on language switches via ``_wire_language_state``).
    """
    ctx: dict[str, str] = {}
    if project_dir:
        ctx["project_dir"] = project_dir
    if workspace_dir:
        ctx["workspace_dir"] = workspace_dir
    return render_native_system_prompt(
        persona=persona, context=ctx or None, language=language
    )


def _wire_language_state(
    state: LanguageState,
    llm_context: Any,
    persona: str,
    project_dir: str | None = None,
    workspace_dir: str | None = None,
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
        new_system = _build_system_prompt(
            persona,
            new_lang,
            project_dir=project_dir,
            workspace_dir=workspace_dir,
        )
        for i, msg in enumerate(messages):
            if isinstance(msg, dict) and msg.get("role") == "system":
                messages[i] = {"role": "system", "content": new_system}
                logger.info("[LLM SYSTEM PROMPT REWRITE] lang=%s", new_lang)
                return
        # No system message yet — prepend.
        messages.insert(0, {"role": "system", "content": new_system})
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
    echo_classifier: Any = None,
    voice_state_observer: Any = None,
    agent_state_observer: Any = None,
    user_aggregator: Any,
    llm_service: Any,
    tts: Any,
    assistant_response_logger: Any = None,
    tts_scrub: Any = None,
    usage_recorder: Any = None,
    tts_fade_observer: Any = None,
    assistant_aggregator: Any,
    system_prompt_injector: Any = None,
    sound_cue_processor: Any = None,
    mute_gate: Any = None,
    input_mute_gate: Any = None,
    interrupt_toggle_gate: Any = None,
    cancel_flag_gate: Any = None,
    echo_gate: Any = None,
    echo_collector: Any = None,
    far_collector: Any = None,
    audio_probes: list | None = None,
    speech_energy_gate: Any = None,
    post_stt_stages: list | None = None,
    post_llm_stages: list | None = None,
    pre_output_stages: list | None = None,
    far_collector_upstream: bool = False,
    input_gain: Any = None,
    output_volume: Any = None,
    sidetone: Any = None,
    aec_filter: Any = None,
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
    # Level taps, when asked for. They cost one numpy reduction per frame
    # and they are how "echo behaves oddly" became four located bugs: a
    # stage that emits -120 dB from a -28 dB input is not filtering, it
    # is deleting, and that is indistinguishable by ear from perfect echo
    # suppression. See docs/findings/measuring.md.
    if audio_probes:
        stages.append(audio_probes[0])
    # input_mute_gate sits as early as possible so muted mic audio is dropped
    # before STT (and any speaker buffering) runs.
    if input_mute_gate is not None:
        stages.append(input_mute_gate)
    # input_gain sits right after input_mute_gate — gain is applied to mic
    # audio before STT.  Hot-reloadable via state key "input_gain".
    if input_gain is not None:
        stages.append(input_gain)
    # interrupt_toggle_gate sits right after input_mute_gate so it can
    # drop mic input while the bot is speaking when the user has disabled
    # barge-in via the interrupt toggle (flag file present).
    if interrupt_toggle_gate is not None:
        stages.append(interrupt_toggle_gate)
    # cancel_flag_gate sits AFTER input_mute_gate (so a cancel during a muted
    # session still fires) and BEFORE every heavy stage — the gate emits
    # InterruptionFrame upstream which propagates immediately as a SystemFrame,
    # so its physical position only matters for the order in which it observes
    # frames, not for cancel latency.
    if cancel_flag_gate is not None:
        stages.append(cancel_flag_gate)
    # aec_filter sits BEFORE echo_gate, not after. The old order ran the
    # correlation gate on the raw microphone signal, where the user's
    # voice is mixed with the bot's echo: correlation measured 0.55-0.78
    # against a 0.15 threshold and 100% of frames were dropped whole
    # during playback, so nothing said over the bot could reach STT and
    # barge-in was impossible by construction. On the AEC residual the
    # echo is already subtracted, so only what survives is judged.
    if aec_filter is not None:
        stages.append(aec_filter)
    if audio_probes and len(audio_probes) > 1:
        stages.append(audio_probes[1])
    # echo_gate is a second line of defence and is off by default now
    # that cancellation actually works — see docs/findings/echo-cancellation.md.
    if echo_gate is not None:
        stages.append(echo_gate)
    # sidetone sits BETWEEN echo_gate and STT. It copies mic audio
    # (16 kHz) to speaker output (24 kHz) so the user can monitor what
    # the agent hears. Gated off during bot speech to prevent feedback.
    if sidetone is not None:
        stages.append(sidetone)
    stages.extend([stt, stt_error_observer])
    # Sits where both the audio and its transcript are visible, so a
    # segment too quiet to be speech can be dropped whatever the
    # transcript claims it said.
    if speech_energy_gate is not None:
        stages.append(speech_energy_gate)
    # Transcriptions exist only between STT and the aggregator, which
    # consumes them — anything watching what was heard has to sit here.
    if post_stt_stages:
        stages.extend(post_stt_stages)
    # voice_state_observer sits BEFORE transcription_gate so it sees every
    # raw TranscriptionFrame (the gate may suppress some for cancel words /
    # debounce). UserStartedSpeaking / UserStoppedSpeaking SystemFrames also
    # flow through this point, giving the dashboard a live state read.
    if voice_state_observer is not None:
        stages.append(voice_state_observer)
    if echo_classifier is not None:
        stages.append(echo_classifier)
    stages.append(transcription_gate)
    if system_prompt_injector is not None:
        stages.append(system_prompt_injector)
    stages.extend([user_aggregator, llm_service])
    # assistant_response_logger sits BETWEEN llm_service and tts so it can
    # observe LLMFullResponseStartFrame / LLMTextFrame / LLMFullResponseEndFrame
    # before TTS consumes them — EdgeTTSService never emits TTSTextFrame, so
    # capture must happen on the LLM side.
    # LLMTextFrame is consumed by TTS, so anything observing the model's
    # words has to sit before it, and anything observing audio after it.
    if post_llm_stages:
        stages.extend(post_llm_stages)
    if assistant_response_logger is not None:
        stages.append(assistant_response_logger)
    # tts_scrub strips tool-name narration before TTS speaks the text.
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
    if agent_state_observer is not None:
        stages.append(agent_state_observer)
    if sound_cue_processor is not None:
        stages.append(sound_cue_processor)
    if mute_gate is not None:
        stages.append(mute_gate)
    # echo_collector sits AFTER mute_gate so it only captures bot audio
    # that is actually being played (muted audio is not echoed).
    if echo_collector is not None:
        stages.append(echo_collector)
    # output_volume sits right before transport.output() so volume is applied
    # to TTS audio before it reaches the speaker.  Hot-reloadable via state
    # key "output_volume".
    if output_volume is not None:
        stages.append(output_volume)
    # With no output device the transport is the end of the road: it
    # forwards nothing, so a tap behind it collects silence. Every
    # scenario run so far handed the canceller an empty reference, and
    # it cancelled nothing — the runs passed anyway, because
    # the gates upstream caught the echo, and the one time they did not
    # the assistant answered its own voice eight times in a row.
    if far_collector_upstream and far_collector is not None:
        stages.append(far_collector)
    if pre_output_stages:
        stages.extend(pre_output_stages)
    stages.append(transport_output)
    # far_collector sits AFTER transport_output, deliberately. Tapped
    # upstream of it, the AEC reference arrives as fast as TTS generates
    # audio: the queue ran 4.6 s deep mid-utterance and emptied in the
    # gaps between sentences, leaving the canceller with silence exactly
    # while the speaker was still playing. Downstream, frames arrive at
    # the rate they are written to the device.
    if far_collector is not None and not far_collector_upstream:
        stages.append(far_collector)
    stages.append(assistant_aggregator)
    return stages


async def build_pipeline(
    settings: Settings,
    store: "TranscriptStore",
    context_builder: "ContextBuilder",
    persona: str = "",
    *,
    state: State | None = None,
    conversation_manager: "ConversationManager | None" = None,
    project_dir: str | None = None,
    memory_backend: "MemoryBackend | None" = None,
    audio: bool = True,
    audio_probes: list | None = None,
    post_stt_stages: list | None = None,
    post_llm_stages: list | None = None,
    pre_output_stages: list | None = None,
) -> Tuple[object, object, object, object, object, object, object, object]:
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
    from pipecat.adapters.schemas.tools_schema import ToolsSchema
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
    )
    from pipecat.processors.frame_processor import (
        FrameProcessor as _FP,
    )
    from pipecat.services.groq.stt import GroqSTTService
    from pipecat.transcriptions.language import Language
    from pipecat.turns.user_stop import (
        SpeechTimeoutUserTurnStopStrategy,
        TurnAnalyzerUserTurnStopStrategy,
    )
    from pipecat.turns.user_start.vad_user_turn_start_strategy import (
        VADUserTurnStartStrategy,
    )
    from pipecat.turns.user_start.wake_phrase_user_turn_start_strategy import (
        WakePhraseUserTurnStartStrategy,
    )
    from pipecat.turns.user_turn_strategies import UserTurnStrategies

    from src.agent.llm.switchable import SwitchableLLMService
    from src.pipeline.transport_fix import (
        FixedLocalAudioTransport,
    )
    from pipecat.transports.local.audio import (
        LocalAudioTransportParams,
    )

    if not settings.groq_api_key:
        logger.warning("GROQ_API_KEY is not set — STT will be unavailable")
    available = get_available(settings)
    if not available:
        logger.warning(
            "No LLM provider configured — LLM will be unavailable. "
            "Set at least one of: %s",
            ", ".join(cfg.api_key_env for cfg in PROVIDERS.values()),
        )

    # ------------------------------------------------------------------
    # Audio + STT + TTS (mostly identical to legacy build_pipeline)
    # ------------------------------------------------------------------
    vad = SileroVADAnalyzer(
        params=VADParams(
            stop_secs=getattr(settings, "vad_stop_secs", 0.2),
            start_secs=getattr(settings, "vad_start_secs", 0.2),
            confidence=getattr(settings, "vad_confidence", 0.5),
            min_volume=getattr(settings, "vad_min_volume", 0.2),
        )
    )
    # Deciding when your turn has ended is the single largest delay in
    # the loop — larger than the model, larger than speech recognition.
    turn_mode = getattr(settings, "turn_end", "sentence")
    turn_wait = getattr(settings, "turn_silence_seconds", 0.7)
    if turn_mode == "smart":
        smart_turn = LocalSmartTurnAnalyzerV3(params=SmartTurnParams(stop_secs=1.0))
        turn_stop_strategy = TurnAnalyzerUserTurnStopStrategy(
            turn_analyzer=smart_turn
        )
    elif turn_mode == "sentence":
        from src.pipeline.turns import sentence_turn_stop_strategy

        turn_stop_strategy = sentence_turn_stop_strategy(user_speech_timeout=turn_wait)
    else:
        turn_stop_strategy = SpeechTimeoutUserTurnStopStrategy(
            user_speech_timeout=turn_wait
        )
    logger.info("turn end: %s (%.2fs)", turn_mode, turn_wait)
    # Pipecat 0.0.108 moved vad_analyzer/turn_analyzer off the transport and
    # onto the LLMUserAggregator (see LLMUserAggregatorParams below).

    # Resolve optional device names to sounddevice indices so audio
    # goes to/from the user's chosen device instead of the system default.
    audio_in_idx: int | None = None
    audio_out_idx: int | None = None
    if settings.audio_input_device or settings.audio_output_device:
        try:
            import sounddevice as _sd

            devices = _sd.query_devices()
            if settings.audio_input_device:
                audio_in_idx = _resolve_device_index(
                    settings.audio_input_device, devices, kind="input"
                )
                if audio_in_idx is None:
                    logger.warning(
                        "audio_input_device=%r not found; using default",
                        settings.audio_input_device,
                    )
            if settings.audio_output_device:
                audio_out_idx = _resolve_device_index(
                    settings.audio_output_device, devices, kind="output"
                )
                if audio_out_idx is None:
                    logger.warning(
                        "audio_output_device=%r not found; using default",
                        settings.audio_output_device,
                    )
            if audio_in_idx is not None or audio_out_idx is not None:
                logger.info(
                    "audio devices: input=%s output=%s",
                    audio_in_idx,
                    audio_out_idx,
                )
        except Exception:
            logger.exception("audio device resolution failed (using defaults)")

    # audio=False opens no devices. The whole chain — model, tools, TTS,
    # timings — can then be driven from injected text and measured
    # without a microphone or a pair of ears, which is the only way this
    # pipeline has ever been testable end to end.
    transport = FixedLocalAudioTransport(
        params=LocalAudioTransportParams(
            audio_in_enabled=audio,
            audio_out_enabled=audio,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=settings.tts_sample_rate,
            input_device_index=audio_in_idx if audio else None,
            output_device_index=audio_out_idx if audio else None,
        )
    )
    _transport_ref["transport"] = transport
    # STT language is a HINT for Groq's Whisper, not a hard force. Groq will detect
    # the language from audio and can override this hint if confident (e.g., English
    # speech despite "uk" hint). The TranscriptionGateProcessor reads Groq's detected
    # language and dynamically updates TTS voice to match.
    stt = GroqSTTService(
        api_key=settings.groq_api_key,
        settings=GroqSTTService.Settings(
            language=Language(settings.groq_language),
        ),
        include_prob_metrics=True,
    )
    tts_cache = TTSCache()
    tts = create_edge_tts_service(
        voice=settings.tts_voice,
        sample_rate=settings.tts_sample_rate,
        cache=tts_cache,
    )

    # ------------------------------------------------------------------
    # Indication subsystem (identical to legacy)
    # ------------------------------------------------------------------
    from src.voice.indication.core import (
        Indication,
        build_sound_cue_processor,
        set_indication,
    )

    sound_cue_processor = None
    backends: list[Any] = []
    if settings.indication.enabled:
        if settings.indication.sound_enabled:
            from src.voice.indication.backends.sound import SoundBackend

            sound_cue_processor = build_sound_cue_processor(
                sample_rate=settings.tts_sample_rate
            )
            backends.append(
                SoundBackend(sound_cue_processor, sample_rate=settings.tts_sample_rate)
            )
        if settings.indication.visual_enabled:
            from src.voice.indication.backends.visual import VisualBackend

            backends.append(VisualBackend(settings.log_dir / "indication.jsonl"))
        if settings.indication.notification_center_enabled:
            from src.voice.indication.backends.notification import NotificationBackend

            backends.append(NotificationBackend())

    def _mode_provider():
        # Late-bound: SessionState is constructed after Indication, so
        # resolve it at notify-time. Falls back to the static settings
        # mode until the pipeline finishes wiring.
        from src.pipeline.session_state import get_active_session_state

        ss = get_active_session_state()
        return ss.profile if ss is not None else settings.mode

    indication = Indication(settings.indication, backends, mode_provider=_mode_provider)
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
                    ind_inner.notify(IndicationKind.STT_ERROR, body=str(err_msg)[:160])
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
                            "pipeline_native: tts.cancel_pending raised (non-fatal)"
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
    # Single source of truth for the active mode. Constructed here so it
    # exists before connect_mcp_servers() and register_all_tools() — both
    # capture it for the execution-time tool-deny gate.
    from src.pipeline.session_state import (
        SessionState,
        set_active_session_state,
    )

    session_state = SessionState(
        language_state, state=state, initial_mode=settings.mode.value
    )
    # Module-level handle for the set_mode direct tool.
    set_active_session_state(session_state)
    bot_speech_state = BotSpeechState()
    transcription_gate = create_transcription_gate(
        wake_phrases=wake_phrases(settings, persona) if settings.wake_required else [],
        store=store,
        settings=settings,
        tts_service=tts,
        language_state=language_state,
        bot_speech_state=bot_speech_state,
        session_state=session_state,
    )
    session_state.register_flush_hook(transcription_gate.force_flush)
    voice_state_observer = create_voice_state_observer(state)
    agent_state_observer = create_agent_state_observer(state)

    if settings.echo_classifier_enabled:
        echo_classifier = create_echo_classifier(
            state=state,
            bot_speech_state=bot_speech_state,
            settings=settings,
        )
        logger.info("LLM echo classifier: enabled")
    else:
        echo_classifier = None
        logger.info("LLM echo classifier: disabled")

    llm_service = SwitchableLLMService(
        zai_api_key=settings.zai_api_key,
        zai_model=settings.zai_model,
        zai_base_url=settings.zai_base_url,
        opencode_api_key=settings.opencode_api_key,
        opencode_base_url=settings.opencode_base_url,
        opencode_model=settings.opencode_model,
        deepseek_api_key=settings.deepseek_api_key,
        deepseek_base_url=settings.deepseek_base_url,
        deepseek_model=settings.deepseek_model,
        state=state,
        settings=settings,
    )
    # The voice/hands split, decided before any schema is built: the
    # conversational model must not be shown sixty-three tools it is not
    # allowed to call, and the prompt's tool catalog reads the same flag.
    from src.agent.hands import Hands, set_hands

    # The context does not exist yet — hands reads it through this holder
    # when a job starts, which is also what keeps it current rather than
    # a snapshot taken at build time.
    _context_ref: dict[str, Any] = {}

    def _recent_turns() -> list[dict]:
        ctx = _context_ref.get("context")
        if ctx is None:
            return []
        try:
            return [m for m in ctx.get_messages() if m.get("role") != "system"]
        except Exception:
            logger.debug("hands: LLMContext messages unavailable")
            return []

    # Mute survives restarts, and a muted microphone is silent in exactly
    # the way a broken one is: input_mute_gate drops the audio before STT
    # and nothing else says a word. A session that ended muted cost a full
    # day of live testing before anyone thought to look at the flag.
    if state is not None and state.get_bool("mute_mic"):
        logger.warning(
            "MICROPHONE IS MUTED (mute_mic=1, persisted from an earlier "
            "session). Nothing will be heard until it is cleared."
        )

    try:
        from src.pipeline.stages.speech_energy_gate import create_speech_energy_gate

        speech_gate = create_speech_energy_gate(
            min_rms=settings.stt_min_rms,
            min_seconds=settings.stt_min_speech_seconds,
        )
        logger.info(
            "speech_energy_gate: active (min_rms=%.0f, min=%.2fs)",
            settings.stt_min_rms,
            settings.stt_min_speech_seconds,
        )
    except Exception:  # noqa: BLE001
        logger.exception("speech_energy_gate: creation failed (non-fatal)")
        speech_gate = None

    job_store = None
    stranded: list = []
    try:
        from src.agent.jobs import JobStore

        job_store = JobStore(store.db)
        await job_store.init()
        # Anything still marked running was cut off by the process ending;
        # nothing else can leave a job in that state.
        stranded = await job_store.sweep_interrupted()
        for job in stranded:
            logger.info("jobs: interrupted by restart — %s", job.task[:120])
    except Exception:  # noqa: BLE001
        logger.exception("jobs: unavailable (non-fatal)")
        job_store = None

    hands = Hands(
        settings,
        llm_service=llm_service,
        session_state=session_state,
        conversation_manager=conversation_manager,
        recent_turns=_recent_turns,
        jobs=job_store,
    )
    set_hands(hands)

    tools_schema = build_tools_schema(session_state=session_state)
    # Connect stdio MCP servers from workspace/.mcp.json and fold their
    # tools into the LLM's surface BEFORE the context is built so the
    # model sees them on turn one. Always returns a bridge (never raises).
    from src.agent.mcp_bridge import connect_mcp_servers
    from src.agent.modes import is_tool_allowed as mode_is_tool_allowed

    mcp_bridge = await connect_mcp_servers(settings)
    # Let the system prompt advertise the *actually connected* tools
    # instead of the static .mcp.json name-only block.
    try:
        context_builder.set_mcp_bridge(mcp_bridge)
    except AttributeError:
        logger.debug("context_builder has no set_mcp_bridge; skipping")
    try:
        context_builder.set_session_state(session_state)
    except AttributeError:
        logger.debug("context_builder has no set_session_state; skipping")
    _mcp_schemas = mcp_bridge.function_schemas()
    _all_schemas = list(tools_schema.standard_tools)
    if _mcp_schemas:
        filtered_mcp = [
            s for s in _mcp_schemas
            if mode_is_tool_allowed(session_state.profile, s.name)
        ]
        _all_schemas.extend(filtered_mcp)
        logger.info(
            "mcp_bridge: %d tool(s) from %d server(s) added to LLM surface"
            " (%d denied by mode profile)",
            len(filtered_mcp),
            len(mcp_bridge.connected_servers),
            len(_mcp_schemas) - len(filtered_mcp),
        )
    tools_schema = ToolsSchema(standard_tools=_all_schemas)
    llm_context = LLMContext(
        messages=[
            {
                "role": "system",
                "content": _build_system_prompt(
                    persona,
                    language_state.language,
                    project_dir=project_dir,
                    workspace_dir=str(settings.workspace_dir),
                ),
            }
        ],
        tools=tools_schema,
    )
    _context_ref["context"] = llm_context

    def _rebuild_tool_schemas(profile: Any) -> None:
        new_schema = build_tools_schema(session_state=session_state)
        filtered_builtins = list(new_schema.standard_tools)
        if _mcp_schemas:
            from src.agent.modes import is_tool_allowed as _mode_is_tool_allowed
            filtered_mcp = [
                s for s in _mcp_schemas
                if _mode_is_tool_allowed(profile, s.name)
            ]
            filtered_builtins.extend(filtered_mcp)
        new_schema = ToolsSchema(standard_tools=filtered_builtins)
        llm_context.tools = new_schema
        logger.info(
            "tool_schema: rebuilt for mode %s (%d tools, %d MCP filtered)",
            profile.name,
            len(filtered_builtins) - len(filtered_mcp),
            len(_mcp_schemas) - len(filtered_mcp),
        )

    session_state.add_mode_change_listener(_rebuild_tool_schemas)

    # Being addressed. The wake strategy goes first and blocks the ones
    # after it until it hears its name in a final transcript; once awake,
    # VAD behaves exactly as before, interruptions included.
    #
    # Speech is still transcribed while asleep — the gate is on acting,
    # not on hearing, which is what makes "listen and remember, answer
    # when asked" possible at all.
    turn_start_strategies: list = []
    if settings.wake_required:
        phrases = wake_phrases(settings, persona)
        gate = WakePhraseUserTurnStartStrategy(
            phrases=phrases,
            timeout=settings.wake_window_seconds,
            single_activation=settings.wake_every_turn,
        )

        # Both events are logged by pipecat at DEBUG, which this daemon
        # never enables — so three days of logs recorded not one wake, and
        # every statement about when the assistant was listening had to be
        # inferred from whether it happened to reply. Subscribing costs
        # nothing and turns the gate from a guess into a measurement.
        #
        # The held time is the number worth having: the window is
        # refreshed by *any* transcription, the room's included, so it
        # reports how long the door stood open, not how long you talked.
        woke_at = 0.0

        @gate.event_handler("on_wake_phrase_detected")
        async def _awake(_gate: Any, phrase: str) -> None:
            nonlocal woke_at
            woke_at = time.monotonic()
            logger.info("wake: awake — heard %r", phrase)

        @gate.event_handler("on_wake_phrase_timeout")
        async def _asleep(_gate: Any) -> None:
            held = time.monotonic() - woke_at if woke_at else 0.0
            logger.info("wake: asleep — the window stood open %.0fs", held)

        turn_start_strategies.append(gate)
        logger.info(
            "wake: required — %s (window %.0fs, every turn: %s)",
            ", ".join(phrases),
            settings.wake_window_seconds,
            settings.wake_every_turn,
        )
    else:
        logger.info("wake: not required — every utterance is treated as addressed")
    turn_start_strategies.append(VADUserTurnStartStrategy(enable_interruptions=True))

    # vad_analyzer + user_turn_strategies migrated here from transport params
    # (deprecated since pipecat 0.0.108).
    aggregator_pair = LLMContextAggregatorPair(
        llm_context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=vad,
            user_turn_strategies=UserTurnStrategies(
                # Interruptions on. They were off because the bot's own
                # audio leaking back through the mic would preempt its
                # own TTS — which was true while echo cancellation was
                # destroying the signal instead of removing the echo.
                # With AEC3 working (40-50 dB measured) a voice heard
                # during playback is the user.
                start=turn_start_strategies,
                stop=[turn_stop_strategy],
            ),
        ),
    )
    user_aggregator = aggregator_pair.user()
    assistant_aggregator = aggregator_pair.assistant()

    # The result of a delegated job re-enters as a user turn, so the
    # conversational model phrases it in the right language and its own
    # voice. inject_user_text also persists the turn and emits the
    # pipeline event, which a raw frame push would skip.
    from src.pipeline.stages.text_injector import make_llm_message_pusher

    hands.set_delivery(make_llm_message_pusher(transcription_gate))

    register_all_tools(
        llm_service,
        settings=settings,
        conversation_manager=conversation_manager,
        session_state=session_state,
    )
    try:
        from src.agent.tools.direct import set_memory_backend

        set_memory_backend(memory_backend)
    except Exception:
        pass
    mcp_registered = mcp_bridge.register(
        llm_service, conversation_manager, session_state=session_state
    )
    if mcp_registered:
        logger.info(
            "mcp_bridge: registered handlers: %s",
            ", ".join(mcp_registered),
        )
    _wire_language_state(
        language_state,
        llm_context,
        persona,
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
        state=state,
        settings=settings,
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
    # The gate owns the current-voice bookkeeping; tts_scrub decides the voice
    # from the assembled reply text (the only reliable signal) and calls the
    # gate's setter, so there is still one owner. Fallback = the conversation's
    # language, for replies with no script signal ("4.").
    tts_scrub = create_tts_scrub_processor(
        voice_setter=getattr(transcription_gate, "_set_tts_voice", None),
        language_fallback=lambda: language_state.language,
    )

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
    mute_gate = create_mute_gate(state=state, session_state=session_state)

    # Input (mic) mute gate — drops InputAudioRawFrame when
    # ``settings.mute_input_file`` exists. Sits at the very front of the
    # pipeline so STT never even sees the muted audio.
    input_mute_gate = create_input_mute_gate(state=state)

    # Interrupt-toggle gate — flag-file-driven barge-in guard. When the
    # user disables interrupt (via the dashboard button → /interrupt API),
    # ``interrupt_enabled_file`` is created and this gate drops mic input
    # while the bot is speaking, letting the bot finish its utterance.
    interrupt_toggle_gate = create_interrupt_toggle_gate(state=state)

    # Input gain processor — multiplies mic audio samples before STT.
    # Reads ``input_gain`` from state (hot-reloadable via mic_gain tool),
    # falls back to settings.input_gain.  Requires state; skipped when
    # state is None (e.g. test builds).
    input_gain_proc = None
    if state is not None:
        input_gain_proc = create_input_gain_processor(settings=settings, state=state)

    # Output volume processor — multiplies TTS audio samples before speaker.
    # Reads ``output_volume`` from state (hot-reloadable via volume tool),
    # falls back to settings.output_volume.
    output_volume_proc = None
    if state is not None:
        output_volume_proc = create_output_volume_processor(settings=settings, state=state)

    # Cancel-flag gate — external "interrupt now" trigger. Mirrors the
    # mute-gate flag-file contract: any process (overlay, watch dashboard,
    # hotkey daemon) touches ``settings.cancel_flag_file`` and the next
    # pipeline frame pushes an InterruptionFrame upstream.
    cancel_flag_gate = create_cancel_flag_gate(state=state)

    # Shared EchoState ring buffer — used by both echo_gate (cross-correlation)
    # and AEC3 filter (adaptive acoustic echo cancellation). Created when at
    # least one of the two subsystems is enabled.
    echo_state: Any = None
    echo_gate_proc = None
    echo_collector = None
    if settings.echo_gate_enabled or settings.aec_enabled:
        from src.pipeline.echo_state import EchoState

        echo_state = EchoState(
            buffer_seconds=settings.echo_gate_buffer_seconds,
            target_sample_rate=16000,
        )

    # Acoustic echo gate — cross-correlates mic input against recent bot
    # output audio and drops correlated frames before they reach STT.
    if settings.echo_gate_enabled and echo_state is not None:
        try:
            from src.pipeline.stages.echo_gate import create_echo_gate_stages

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

    # WebRTC AEC3 acoustic echo cancellation. The implementation lives in
    # src/core/aec.py — the dependency points that way on purpose, since
    # this tree is the one destined for deletion.
    #
    # The stage it replaces never cancelled anything. It passed
    # int16-scaled values into an API that expects [-1, 1], so every
    # sample was clipped to ±1 LSB (about −90 dB) and the microphone went
    # silent for as long as the bot spoke. It also ran only during bot
    # speech, on variable frame lengths, so AEC3's adaptive filter
    # restarted every utterance and never converged.
    # See docs/findings/echo-cancellation.md.
    aec_filter_proc = None
    far_end_collector = None
    if settings.aec_enabled:
        try:
            from src.core.aec import (
                FarEnd,
                make_continuous_aec,
                make_far_end_collector,
            )

            far_end = FarEnd()
            far_end_collector = make_far_end_collector(far_end)
            aec_filter_proc = make_continuous_aec(
                far_end,
                stream_delay_ms=settings.aec_stream_delay_ms,
                noise_suppression=settings.aec_noise_suppression,
                measure_delay=settings.aec_measure_delay,
            )
            logger.info(
                "AEC3 filter: active (stream_delay=%dms, ns=%s, measure=%s)",
                settings.aec_stream_delay_ms,
                settings.aec_noise_suppression,
                settings.aec_measure_delay,
            )
        except Exception:  # noqa: BLE001
            logger.exception("AEC3 filter: creation failed (non-fatal)")
            aec_filter_proc = None
            far_end_collector = None

    if audio_probes is None and settings.audio_probe_enabled:
        try:
            from src.core.audio_probe import make_audio_probe

            # bot_speaking comes from FarEnd, which sits at the output and
            # therefore knows. The echo gate used to own that flag, and it
            # is off by default now.
            audio_probes = [
                make_audio_probe("raw", far_end),
                make_audio_probe("post-aec", far_end),
            ]
            logger.info("audio_probe: level taps active (raw, post-aec)")
        except Exception:  # noqa: BLE001
            logger.exception("audio_probe: creation failed (non-fatal)")
            audio_probes = None

    # ------------------------------------------------------------------
    # Sidetone — copies mic input to speaker so the user can monitor
    # exactly what the agent hears.  Sits between echo_gate and STT.
    # Gate is gated OFF during bot speech by the processor internally.
    # ------------------------------------------------------------------
    sidetone = SidetoneProcessor(
        state=state,
        settings=settings,
        sample_rate_in=16000,
        sample_rate_out=settings.tts_sample_rate,
        volume=settings.sidetone_volume,
    )

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
        far_collector=far_end_collector,
        audio_probes=audio_probes,
        speech_energy_gate=speech_gate,
        post_stt_stages=post_stt_stages,
        post_llm_stages=post_llm_stages,
        pre_output_stages=pre_output_stages,
        far_collector_upstream=not audio,
        aec_filter=aec_filter_proc,
        sidetone=sidetone,
        input_gain=input_gain_proc,
        output_volume=output_volume_proc,
    )

    logger.info(
        "Pipecat-native pipeline assembled: provider=%s, model=%s, lang=%s, tools=%d",
        llm_service.active_provider,
        getattr(
            settings,
            f"{llm_service.active_provider}_model",
            PROVIDERS[llm_service.active_provider].default_model,
        ),
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
