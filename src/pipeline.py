"""Pipecat pipeline assembly.

Pipecat imports are deferred inside build_pipeline so admin CLI paths work
without portaudio installed.

Phase 2.1: single generator pipeline; `generator_mode` flag retired.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Tuple

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
    conversation_manager: Any = None,
    *,
    speaker_gallery: Any = None,
    speaker_model: Any = None,
    namer_enqueue: Any = None,
) -> Tuple[object, object, object, object]:
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

    from pipecat.transcriptions.language import Language

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
        conversation_manager=conversation_manager,
        tts_service=tts,
    )

    speaker_buffer = None
    speaker_tagger = None
    if (
        settings.speaker_id_enabled
        and speaker_gallery is not None
        and speaker_model is not None
    ):
        from .speaker_processor import create_speaker_processors

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

    # Build the indication subsystem (sound + visual + macOS notifications).
    # Indication is constructed even when settings.indication.enabled is False
    # so producers can call notify() unconditionally; the facade short-circuits.
    from .indication import Indication, build_sound_cue_processor

    sound_cue_processor = None
    backends: list[Any] = []
    if settings.indication.enabled:
        if settings.indication.sound_enabled:
            from .indication_backends.sound import SoundBackend

            sound_cue_processor = build_sound_cue_processor(
                sample_rate=settings.tts_sample_rate
            )
            backends.append(
                SoundBackend(sound_cue_processor, sample_rate=settings.tts_sample_rate)
            )
        if settings.indication.visual_enabled:
            from .indication_backends.visual import VisualBackend

            backends.append(
                VisualBackend(settings.log_dir / "indication.jsonl")
            )
        if settings.indication.notification_center_enabled:
            from .indication_backends.notification import NotificationBackend

            backends.append(NotificationBackend())
    indication = Indication(
        settings.indication,
        backends,
        mode_provider=lambda: settings.mode,
    )
    # Register as process-wide singleton so producers in any module can
    # fire notify() without threading the facade through call sites.
    from .indication import set_indication

    set_indication(indication)
    logger.info(
        "indication: %d backend(s) ready (enabled=%s)",
        len(backends),
        settings.indication.enabled,
    )

    # STT error-frame observer — pipecat surfaces STT errors via ErrorFrame.
    # A one-line wrapper FrameProcessor inserted immediately downstream of stt
    # forwards the frame and fires STT_ERROR on the indication facade.
    from pipecat.frames.frames import ErrorFrame  # type: ignore
    from pipecat.processors.frame_processor import (  # type: ignore
        FrameProcessor as _FP,
    )

    class _SttErrorObserver(_FP):  # type: ignore[misc,valid-type]
        async def process_frame(self, frame, direction) -> None:  # type: ignore[override]
            await super().process_frame(frame, direction)
            if isinstance(frame, ErrorFrame):
                from .indication import IndicationKind, get_indication

                ind_inner = get_indication()
                if ind_inner is not None:
                    err_msg = getattr(frame, "error", str(frame))
                    ind_inner.notify(
                        IndicationKind.STT_ERROR, body=str(err_msg)[:160]
                    )
            await self.push_frame(frame, direction)

    stt_error_observer = _SttErrorObserver()

    stages: list[Any] = [transport.input()]
    if speaker_buffer is not None:
        stages.append(speaker_buffer)
    stages.append(stt)
    stages.append(stt_error_observer)
    if speaker_tagger is not None:
        stages.append(speaker_tagger)
    stages.extend([generator, tts])
    if sound_cue_processor is not None:
        stages.append(sound_cue_processor)
    stages.append(transport.output())
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
    return task, generator, tts_cache, indication
