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
from .tts_cache import TTSCache
from .tts_edge import create_edge_tts_service

if TYPE_CHECKING:
    from .claude_cli import ClaudeCLI
    from .context import ContextBuilder
    from .storage import TranscriptStore
    from .conversation import ConversationManager


logger = logging.getLogger("heare.pipeline")


async def build_pipeline(
    settings: Settings,
    claude_cli: "ClaudeCLI",
    store: "TranscriptStore",
    context_builder: "ContextBuilder",
    conversation_manager: "ConversationManager | None" = None,
) -> Tuple[object, object]:
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

    decider_prompt = (
        Path(__file__).parent.parent / "prompts" / "decider.txt"
    ).read_text()

    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams

    # VAD waits 0.5s of silence before declaring end-of-speech. Compromise
    # between latency (was 1.0s default) and Groq STT rate-limit pressure
    # (0.2s produced too many fragments and hit the free-tier 20 RPS cap).
    # SmartTurnV3's ML model is the anti-fragmentation safety net.
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

    turn_aggregator = None
    if settings.turn_aggregation_enabled:
        from .turn_aggregator import TurnAggregator

        async def on_turn_complete(
            aggregated_text: str,
            turn_start_ts: float,
            turn_end_ts: float,
            buffer: list[dict],
        ) -> None:
            if conversation_manager and settings.topic_extraction_enabled:
                try:
                    topics = await conversation_manager.extract_topics(aggregated_text)
                    conv_id = await conversation_manager.get_or_create_active()
                    await conversation_manager.update_summary(conv_id, aggregated_text, topics)
                    logger.info(
                        "Turn complete: %d utterances, %d topics, conv_id=%s",
                        len(buffer), len(topics), conv_id,
                    )
                except Exception as e:
                    logger.warning("Failed to update conversation on turn complete: %s", e)

        turn_aggregator = TurnAggregator(
            mode=settings.mode,
            focus_timeout=settings.focus_mode_turn_timeout,
            ambient_timeout=settings.ambient_mode_turn_timeout,
            max_turn_duration=settings.max_turn_duration,
            on_turn_complete=on_turn_complete,
        )
        logger.info(
            "TurnAggregator enabled: mode=%s, focus_timeout=%ss, ambient_timeout=%ss",
            settings.mode.value,
            settings.focus_mode_turn_timeout,
            settings.ambient_mode_turn_timeout,
        )

    decider = create_decider_processor(
        claude_cli=claude_cli,
        store=store,
        context_builder=context_builder,
        settings=settings,
        decider_prompt_template=decider_prompt,
        turn_aggregator=turn_aggregator,
        conversation_manager=conversation_manager,
    )

    tts_cache = TTSCache()
    tts = create_edge_tts_service(
        voice=settings.tts_voice,
        sample_rate=settings.tts_sample_rate,
        cache=tts_cache,
    )

    stages: list[object] = [transport.input(), stt]
    if settings.speaker_id_enabled:
        from . import speaker_id
        from .speaker_gallery import SpeakerGallery
        from .speaker_processor import create_speaker_processors

        gallery = SpeakerGallery.load(settings.speakers_file)
        if "owner" not in gallery.list_speakers():
            logger.warning(
                "speaker_id_enabled but no owner enrolled in %s — "
                "run `heare enroll-owner` to enable speaker identification. "
                "Commands still work via keyword gate.",
                settings.speakers_file,
            )
        else:
            model = speaker_id.load_model()
            speaker_id.warmup(model, sample_rate=16000)
            audio_buffer, speaker_tagger = create_speaker_processors(
                settings, gallery, model, sample_rate=16000
            )
            # AudioBufferProcessor sits right after transport.input() so it
            # sees raw PCM; the tagger sits between STT and decider so it
            # can annotate each TranscriptionFrame before the decider reads
            # the speaker_id attribute.
            stages = [transport.input(), audio_buffer, stt, speaker_tagger]

    stages.extend([decider, tts, transport.output()])
    pipeline = Pipeline(stages)
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=False,  # no HW echo cancellation — own voice would kill TTS
        ),
        cancel_on_idle_timeout=False,
        enable_turn_tracking=False,
    )
    return task, decider, tts_cache
