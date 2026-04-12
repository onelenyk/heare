"""Pipecat pipeline assembly.

Pipecat imports are deferred inside build_pipeline so admin CLI paths work
without portaudio installed.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Tuple

from .config import Settings
from .decider import create_decider_processor
from .tts_edge import create_edge_tts_service

if TYPE_CHECKING:
    from .claude_cli import ClaudeCLI
    from .context import ContextBuilder
    from .storage import TranscriptStore


logger = logging.getLogger("heare.pipeline")


async def build_pipeline(
    settings: Settings,
    claude_cli: "ClaudeCLI",
    store: "TranscriptStore",
    context_builder: "ContextBuilder",
) -> Tuple[object, object]:
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import (
        LocalSmartTurnAnalyzerV3,
    )
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.task import PipelineTask
    from pipecat.services.groq.stt import GroqSTTService
    from pipecat.transports.local.audio import (
        LocalAudioTransport,
        LocalAudioTransportParams,
    )

    if not settings.groq_api_key:
        raise RuntimeError(
            "GROQ_API_KEY is not set — copy .env.example to .env and fill it in"
        )

    decider_prompt = (
        Path(__file__).parent.parent / "prompts" / "decider.txt"
    ).read_text()

    vad = SileroVADAnalyzer()
    smart_turn = LocalSmartTurnAnalyzerV3()

    transport = LocalAudioTransport(
        params=LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=vad,
            turn_analyzer=smart_turn,
        )
    )

    stt = GroqSTTService(
        api_key=settings.groq_api_key,
        language=settings.groq_language,
    )

    decider = create_decider_processor(
        claude_cli=claude_cli,
        store=store,
        context_builder=context_builder,
        settings=settings,
        decider_prompt_template=decider_prompt,
    )

    tts = create_edge_tts_service(
        voice=settings.tts_voice,
        sample_rate=settings.tts_sample_rate,
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            decider,
            tts,
            transport.output(),
        ]
    )
    task = PipelineTask(
        pipeline,
        cancel_on_idle_timeout=False,
        enable_turn_tracking=False,
    )
    return task, decider
