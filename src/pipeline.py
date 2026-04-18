"""Pipecat pipeline assembly.

Pipecat imports are deferred inside build_pipeline so admin CLI paths work
without portaudio installed.

Phase 2.1: single generator pipeline; `generator_mode` flag retired.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Tuple

from .config import Settings
from .tts_cache import TTSCache
from .tts_edge import create_edge_tts_service

if TYPE_CHECKING:
    from .actions import IntentQueue
    from .claude_cli import ClaudeCLI
    from .context import ContextBuilder
    from .openrouter_cli import OpenRouterCLI
    from .storage import TranscriptStore


logger = logging.getLogger("heare.pipeline")


async def build_pipeline(
    settings: Settings,
    claude_cli: "ClaudeCLI",
    store: "TranscriptStore",
    context_builder: "ContextBuilder",
    openrouter_cli: "OpenRouterCLI",
    persona: str = "",
    intent_queue: "IntentQueue | None" = None,
) -> Tuple[object, object, object]:
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import (
        LocalSmartTurnAnalyzerV3,
    )
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.services.groq.stt import GroqSTTService
    from pipecat.transports.local.audio import (
        LocalAudioTransport,
        LocalAudioTransportParams,
    )

    if not settings.groq_api_key:
        raise RuntimeError(
            "GROQ_API_KEY is not set — copy .env.example to .env and fill it in"
        )

    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams

    # VAD waits 0.5s of silence before declaring end-of-speech. Compromise
    # between latency (was 1.0s default) and Groq STT rate-limit pressure.
    vad = SileroVADAnalyzer(
        params=VADParams(stop_secs=0.5, start_secs=0.3, confidence=0.7, min_volume=0.6)
    )
    smart_turn = LocalSmartTurnAnalyzerV3(params=SmartTurnParams(stop_secs=1.0))

    transport = LocalAudioTransport(
        params=LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=16000,
            vad_analyzer=vad,
            turn_analyzer=smart_turn,
        )
    )

    stt = GroqSTTService(
        api_key=settings.groq_api_key,
        language=settings.groq_language,
    )

    tts_cache = TTSCache()
    tts = create_edge_tts_service(
        voice=settings.tts_voice,
        sample_rate=settings.tts_sample_rate,
        cache=tts_cache,
    )

    from .generator import create_generator_processor

    generator_prompt = (
        Path(__file__).parent.parent / "prompts" / "generator.txt"
    ).read_text()
    generator = create_generator_processor(
        openrouter_cli=openrouter_cli,
        context_builder=context_builder,
        prompt_template=generator_prompt,
        persona=persona,
        store=store,
        settings=settings,
        intent_queue=intent_queue,
    )

    stages = [transport.input(), stt, generator, tts, transport.output()]
    logger.info(
        "Generator pipeline: model=%s, openrouter_timeout=%ss, action_timeout=%ss",
        settings.openrouter_model,
        settings.openrouter_timeout_seconds,
        settings.action_timeout_seconds,
    )
    pipeline = Pipeline(stages)
    task = PipelineTask(
        pipeline,
        params=PipelineParams(allow_interruptions=False),
        cancel_on_idle_timeout=False,
        enable_turn_tracking=False,
    )
    return task, generator, tts_cache
