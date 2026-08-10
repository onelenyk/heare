"""The pipeline — ten stages instead of twenty-seven.

What is gone and why:

* the five observers        → one log line where the thing happens
* the mode gates            → there are no modes
* echo_classifier           → it awaited a 5 s DeepSeek call inside
                              ``process_frame``, and only while the user
                              was interrupting: the one path that must
                              never wait was the only one that did
* smart-turn analyzer       → SpeechTimeoutUserTurnStopStrategy ends a
                              turn on VAD silence, without transformers
* system_prompt_injector,
  context_injector          → the prompt is built once, in prompt.py
* indication, canvas,
  display, dashboard        → they assume a screen

What is kept, because it is the good part and it works: WebRTC AEC3, the
correlation echo gate, and the Groq/Edge services.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def build(
    settings: Any,
    state: Any,
    memory: Any,
    *,
    audio: bool = True,
    split: bool = True,
    probe_audio: bool = False,
    use_aec: bool = True,
    use_gate: bool = False,
    extra_stages: list[Any] | None = None,
    post_llm_stages: list[Any] | None = None,
) -> tuple[Any, Any]:
    """Return ``(task, llm_service)`` ready to run.

    ``audio=False`` opens no devices: the transport is built with input
    and output disabled so the whole pipeline — model, tools, TTS,
    timings — can be driven from text and measured without a microphone
    or a pair of ears. ``extra_stages`` are appended just before the
    output transport, which is where a capture probe belongs.
    """
    from pipecat.adapters.schemas.tools_schema import ToolsSchema  # noqa: F401
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
    )
    from pipecat.services.groq.stt import GroqSTTService
    from pipecat.transcriptions.language import Language
    from pipecat.transports.local.audio import LocalAudioTransportParams
    from pipecat.turns.user_start.vad_user_turn_start_strategy import (
        VADUserTurnStartStrategy,
    )
    from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
    from pipecat.turns.user_turn_strategies import UserTurnStrategies

    from pipecat.frames.frames import LLMMessagesAppendFrame

    from src.agent.llm.switchable import SwitchableLLMService
    from src.core import tools as core_tools
    from src.core.aec import FarEnd, make_continuous_aec, make_far_end_collector
    from src.core.agent import Hands
    from src.core.prompt import build_system_prompt
    from src.pipeline.echo_state import EchoState
    from src.pipeline.stages.echo_gate import create_echo_gate_stages
    from src.pipeline.transport_fix import FixedLocalAudioTransport
    from src.voice.tts.cache import TTSCache
    from src.voice.tts.edge import create_edge_tts_service

    # ── ears ──────────────────────────────────────────────────────────
    vad = SileroVADAnalyzer(
        params=VADParams(
            confidence=settings.vad_confidence,
            start_secs=settings.vad_start_secs,
            stop_secs=settings.vad_stop_secs,
            min_volume=settings.vad_min_volume,
        )
    )
    transport = FixedLocalAudioTransport(
        params=LocalAudioTransportParams(
            audio_in_enabled=audio,
            audio_out_enabled=audio,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=settings.tts_sample_rate,
            input_device_index=_device(settings.audio_input_device, "input")
            if audio
            else None,
            output_device_index=_device(settings.audio_output_device, "output")
            if audio
            else None,
        )
    )
    stt = GroqSTTService(
        api_key=settings.groq_api_key,
        settings=GroqSTTService.Settings(language=Language(settings.groq_language)),
    )

    # ── not hearing ourselves ─────────────────────────────────────────
    # EchoState stays only to keep the correlation gate and the level
    # probes fed; the cancellation itself runs off FarEnd, which is
    # drained in lockstep with the microphone.
    echo_state = EchoState()
    echo_collector, echo_gate = create_echo_gate_stages(echo_state, settings)
    far = FarEnd()
    far_collector = make_far_end_collector(far)
    aec = make_continuous_aec(
        far,
        stream_delay_ms=settings.aec_stream_delay_ms,
        noise_suppression=settings.aec_noise_suppression,
        measure_delay=probe_audio,
    )

    # ── mouth ─────────────────────────────────────────────────────────
    tts = create_edge_tts_service(
        voice=settings.tts_voice,
        sample_rate=settings.tts_sample_rate,
        cache=TTSCache(),
    )

    # ── brain ─────────────────────────────────────────────────────────
    llm = SwitchableLLMService(
        zai_api_key=settings.zai_api_key,
        zai_model=settings.zai_model,
        zai_base_url=settings.zai_base_url,
        opencode_api_key="",
        opencode_base_url="",
        opencode_model="",
        deepseek_api_key=settings.deepseek_api_key,
        deepseek_base_url=settings.deepseek_base_url,
        deepseek_model=settings.deepseek_model,
        state=state,
        settings=settings,
    )
    hands = Hands(settings)
    registered = core_tools.register(
        llm, settings=settings, memory=memory, hands=hands, voice_only=split
    )
    logger.info(
        "voice tools: %d (%s) — hands has %d more",
        len(registered),
        ", ".join(registered),
        len(core_tools.REGISTRY) - len(registered),
    )

    context = LLMContext(
        messages=[
            {
                "role": "system",
                "content": build_system_prompt(
                    str(settings.workspace_dir), split=split
                ),
            }
        ],
        tools=core_tools.schema(voice_only=split),
    )
    pair = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=vad,
            user_turn_strategies=UserTurnStrategies(
                # Interruptions are on: AEC keeps our own voice out of the
                # mic, so a detected voice during playback is the user.
                start=[VADUserTurnStartStrategy(enable_interruptions=True)],
                stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.6)],
            ),
        ),
    )

    # AEC first, then the correlation gate. The old order put the gate on
    # the raw microphone signal, where the user's voice is mixed with the
    # bot's echo — correlation ran 0.55–0.77 against a 0.15 threshold and
    # 88% of frames were dropped whole. Nothing the user said during
    # playback reached STT, so barge-in could not happen at all. On the
    # AEC residual the echo is already subtracted, so what correlates is
    # only what is left over.
    taps: list[Any] = []
    if probe_audio:
        from src.core.audio_probe import make_audio_probe

        taps = [
            make_audio_probe("raw", far),
            make_audio_probe("post-aec", far),
            make_audio_probe("post-gate", far),
        ]

    stages = [
        transport.input(),
        *taps[:1],
        *([aec] if use_aec else []),
        *taps[1:2],
        *([echo_gate] if use_gate else []),
        *taps[2:3],
        stt,
        pair.user(),
        llm,
        *(post_llm_stages or []),
        tts,
        echo_collector,
        *(extra_stages or []),
        transport.output(),
        # The reference is tapped AFTER the output transport, not before:
        # upstream of it, frames arrive as fast as TTS generates them, so
        # the queue ran 4.6 s deep during an utterance and emptied in the
        # gaps between sentences — AEC was handed silence exactly while
        # the speaker was still playing. Downstream, frames come at the
        # rate they are actually written to the device.
        far_collector,
        pair.assistant(),
    ]
    task = PipelineTask(
        Pipeline(stages),
        params=PipelineParams(
            allow_interruptions=True,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    async def deliver(result: str) -> None:
        """A finished job re-enters as a user turn, so the voice agent
        phrases it itself — in the right language, in its own voice."""
        await task.queue_frames(
            [
                LLMMessagesAppendFrame(
                    messages=[
                        {
                            "role": "user",
                            "content": f"[результат роботи] {result}",
                        }
                    ],
                    run_llm=True,
                )
            ]
        )

    hands.set_delivery(deliver)
    logger.info("pipeline: %d stages", len(stages))
    return task, llm


def _device(name: str, kind: str) -> int | None:
    """Resolve a device name to a sounddevice index, or None for default."""
    if not name:
        return None
    import sounddevice as sd

    for idx, dev in enumerate(sd.query_devices()):
        channels = dev["max_input_channels" if kind == "input" else "max_output_channels"]
        if channels > 0 and name.lower() in dev["name"].lower():
            return idx
    logger.warning("audio %s device %r not found; using default", kind, name)
    return None
