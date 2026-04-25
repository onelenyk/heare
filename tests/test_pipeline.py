"""Tests for src/pipeline.py build_pipeline() with all Pipecat classes mocked."""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from src.config import Settings


def _make_pipecat_modules() -> dict:
    """Return a sys.modules patch dict that stubs every pipecat import used by build_pipeline."""
    # Root pipecat package — must be marked as a package (has __path__) so
    # `from pipecat.foo.bar import X` traversal works through the mocked dict.
    pipecat_pkg = types.ModuleType("pipecat")
    pipecat_pkg.__path__ = []  # type: ignore[attr-defined]

    # pipecat.audio.vad.silero — SileroVADAnalyzer
    silero_mod = types.ModuleType("pipecat.audio.vad.silero")
    silero_mod.SileroVADAnalyzer = MagicMock(name="SileroVADAnalyzer")  # type: ignore[attr-defined]

    # pipecat.audio.turn.smart_turn.local_smart_turn_v3 — LocalSmartTurnAnalyzerV3
    smart_turn_mod = types.ModuleType(
        "pipecat.audio.turn.smart_turn.local_smart_turn_v3"
    )
    smart_turn_mod.LocalSmartTurnAnalyzerV3 = MagicMock(  # type: ignore[attr-defined]
        name="LocalSmartTurnAnalyzerV3"
    )

    # pipecat.audio.turn.smart_turn.base_smart_turn — SmartTurnParams
    base_smart_turn_mod = types.ModuleType(
        "pipecat.audio.turn.smart_turn.base_smart_turn"
    )
    base_smart_turn_mod.SmartTurnParams = MagicMock(name="SmartTurnParams")  # type: ignore[attr-defined]

    # pipecat.audio.vad.vad_analyzer — VADParams
    vad_params_mod = types.ModuleType("pipecat.audio.vad.vad_analyzer")
    vad_params_mod.VADParams = MagicMock(name="VADParams")  # type: ignore[attr-defined]

    # pipecat.pipeline.pipeline — Pipeline
    pipeline_mod = types.ModuleType("pipecat.pipeline.pipeline")
    pipeline_mod.Pipeline = MagicMock(name="Pipeline")  # type: ignore[attr-defined]

    # pipecat.pipeline.task — PipelineParams, PipelineTask
    task_mod = types.ModuleType("pipecat.pipeline.task")
    task_mod.PipelineParams = MagicMock(name="PipelineParams")  # type: ignore[attr-defined]
    task_mod.PipelineTask = MagicMock(name="PipelineTask")  # type: ignore[attr-defined]

    # pipecat.transcriptions.language — Language enum
    transcriptions_pkg = types.ModuleType("pipecat.transcriptions")
    transcriptions_pkg.__path__ = []  # type: ignore[attr-defined]
    language_mod = types.ModuleType("pipecat.transcriptions.language")

    class _StubLanguage:
        """Minimal Language stub: Language("en") -> Language instance with .value."""
        def __init__(self, code: str):
            self.value = code
        def __eq__(self, other):
            return isinstance(other, _StubLanguage) and self.value == other.value
        def __repr__(self):
            return f"Language({self.value!r})"
        EN = None  # filled below

    _StubLanguage.EN = _StubLanguage("en")  # type: ignore[attr-defined]
    language_mod.Language = _StubLanguage  # type: ignore[attr-defined]

    # pipecat.services.groq.stt — GroqSTTService
    groq_mod = types.ModuleType("pipecat.services.groq.stt")
    groq_mod.GroqSTTService = MagicMock(name="GroqSTTService")  # type: ignore[attr-defined]

    # pipecat.transports.local.audio — LocalAudioTransport, LocalAudioTransportParams
    transport_mod = types.ModuleType("pipecat.transports.local.audio")
    transport_mod.LocalAudioTransport = MagicMock(name="LocalAudioTransport")  # type: ignore[attr-defined]
    transport_mod.LocalAudioTransportParams = MagicMock(name="LocalAudioTransportParams")  # type: ignore[attr-defined]

    # pipecat.frames.frames — SystemFrame (used by src/indication.py at module top)
    frames_pkg = types.ModuleType("pipecat.frames")
    frames_pkg.__path__ = []  # type: ignore[attr-defined]
    frames_mod = types.ModuleType("pipecat.frames.frames")

    class _StubFrame:
        pass

    class _StubSystemFrame(_StubFrame):
        pass

    class _StubErrorFrame(_StubFrame):
        def __init__(self, error=None):
            self.error = error

    class _StubTTSAudioRawFrame(_StubFrame):
        def __init__(self, *a, **kw):
            pass

    class _StubTTSStoppedFrame(_StubFrame):
        pass

    frames_mod.Frame = _StubFrame  # type: ignore[attr-defined]
    frames_mod.SystemFrame = _StubSystemFrame  # type: ignore[attr-defined]
    frames_mod.ErrorFrame = _StubErrorFrame  # type: ignore[attr-defined]
    frames_mod.TTSAudioRawFrame = _StubTTSAudioRawFrame  # type: ignore[attr-defined]
    frames_mod.TTSStoppedFrame = _StubTTSStoppedFrame  # type: ignore[attr-defined]

    # pipecat.processors.frame_processor — needed by pipeline.py's STT error
    # observer (subclasses FrameProcessor). Stub with a permissive base class.
    processors_pkg = types.ModuleType("pipecat.processors")
    processors_pkg.__path__ = []  # type: ignore[attr-defined]
    fp_mod = types.ModuleType("pipecat.processors.frame_processor")

    class _StubFrameProcessor:
        def __init__(self, *a, **kw):
            pass

        async def process_frame(self, frame, direction):
            return None

        async def push_frame(self, frame, direction=None):
            return None

    class _StubFrameDirection:
        DOWNSTREAM = 1
        UPSTREAM = 2

    fp_mod.FrameProcessor = _StubFrameProcessor  # type: ignore[attr-defined]
    fp_mod.FrameDirection = _StubFrameDirection  # type: ignore[attr-defined]

    # Mark sub-packages as packages too so traversal works for any
    # `from pipecat.x.y import Z` indirectly hit by indication imports.
    for sub in ("audio", "audio.vad", "audio.turn", "audio.turn.smart_turn",
                "pipeline", "services", "services.groq",
                "transports", "transports.local"):
        pass  # markers below already serve as the modules; __path__ added below

    return {
        "pipecat": pipecat_pkg,
        "pipecat.audio": types.ModuleType("pipecat.audio"),
        "pipecat.audio.vad": types.ModuleType("pipecat.audio.vad"),
        "pipecat.audio.vad.silero": silero_mod,
        "pipecat.audio.vad.vad_analyzer": vad_params_mod,
        "pipecat.audio.turn": types.ModuleType("pipecat.audio.turn"),
        "pipecat.audio.turn.smart_turn": types.ModuleType(
            "pipecat.audio.turn.smart_turn"
        ),
        "pipecat.audio.turn.smart_turn.local_smart_turn_v3": smart_turn_mod,
        "pipecat.audio.turn.smart_turn.base_smart_turn": base_smart_turn_mod,
        "pipecat.pipeline": types.ModuleType("pipecat.pipeline"),
        "pipecat.pipeline.pipeline": pipeline_mod,
        "pipecat.pipeline.task": task_mod,
        "pipecat.services": types.ModuleType("pipecat.services"),
        "pipecat.services.groq": types.ModuleType("pipecat.services.groq"),
        "pipecat.services.groq.stt": groq_mod,
        "pipecat.transports": types.ModuleType("pipecat.transports"),
        "pipecat.transports.local": types.ModuleType("pipecat.transports.local"),
        "pipecat.transports.local.audio": transport_mod,
        "pipecat.frames": frames_pkg,
        "pipecat.frames.frames": frames_mod,
        "pipecat.processors": processors_pkg,
        "pipecat.processors.frame_processor": fp_mod,
        "pipecat.transcriptions": transcriptions_pkg,
        "pipecat.transcriptions.language": language_mod,
    }


@pytest.fixture
def pipecat_mocks():
    """Inject stubbed pipecat modules into sys.modules for the duration of a test."""
    mocks = _make_pipecat_modules()
    with patch.dict(sys.modules, mocks):
        yield mocks


@pytest.fixture
def settings():
    s = Settings()
    s.groq_api_key = "test-key"
    s.groq_language = "uk"
    s.tts_voice = "uk-UA-PolinaNeural"
    s.tts_sample_rate = 24000
    # Disable indication backends so pipeline tests don't need to mock the
    # SoundCueProcessor's pipecat-frame import chain.
    s.indication.enabled = False
    return s


@pytest.fixture
def fake_deps():
    # claude_cli, store, context_builder, openrouter_cli
    return MagicMock(), MagicMock(), MagicMock(), MagicMock()


async def test_build_pipeline_raises_on_missing_groq_key(pipecat_mocks, fake_deps) -> None:
    s = Settings()
    s.groq_api_key = None
    cli, store, ctx, openrouter = fake_deps
    from src.pipeline import build_pipeline

    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        await build_pipeline(s, cli, store, ctx, openrouter)


async def test_build_pipeline_returns_task_generator_and_cache(
    pipecat_mocks, settings, fake_deps
) -> None:
    """Phase 2.1: middle element is the generator (no more decider branch)."""
    cli, store, ctx, openrouter = fake_deps

    mock_task_instance = MagicMock(name="PipelineTaskInstance")
    PipelineTask = pipecat_mocks["pipecat.pipeline.task"].PipelineTask
    PipelineTask.return_value = mock_task_instance

    mock_generator = MagicMock(name="GeneratorProcessor")
    with patch("src.generator.create_generator_processor", return_value=mock_generator), \
         patch("src.pipeline.create_edge_tts_service", return_value=MagicMock()), \
         patch("src.tts_edge._build_edge_tts_class", return_value=MagicMock()):
        from src.pipeline import build_pipeline
        from src.tts_cache import TTSCache

        task, processor, tts_cache, _indication = await build_pipeline(
            settings, cli, store, ctx, openrouter
        )

    assert task is mock_task_instance
    assert processor is mock_generator
    assert isinstance(tts_cache, TTSCache)
    assert len(tts_cache) == 0


async def test_pipeline_wiring_order(
    pipecat_mocks, settings, fake_deps
) -> None:
    """Pipeline stages: transport.input → stt → generator → tts → transport.output."""
    cli, store, ctx, openrouter = fake_deps

    transport_cls = pipecat_mocks["pipecat.transports.local.audio"].LocalAudioTransport
    mock_transport_instance = MagicMock(name="transport")
    mock_input = MagicMock(name="input_frame")
    mock_output = MagicMock(name="output_frame")
    mock_transport_instance.input.return_value = mock_input
    mock_transport_instance.output.return_value = mock_output
    transport_cls.return_value = mock_transport_instance

    mock_stt = MagicMock(name="stt")
    pipecat_mocks["pipecat.services.groq.stt"].GroqSTTService.return_value = mock_stt

    mock_generator = MagicMock(name="generator")
    mock_tts = MagicMock(name="tts")

    Pipeline = pipecat_mocks["pipecat.pipeline.pipeline"].Pipeline
    Pipeline.return_value = MagicMock(name="pipeline_instance")

    with patch("src.generator.create_generator_processor", return_value=mock_generator), \
         patch("src.tts_edge._build_edge_tts_class", return_value=MagicMock(return_value=mock_tts)):
        from src.pipeline import build_pipeline

        await build_pipeline(settings, cli, store, ctx, openrouter)

    Pipeline.assert_called_once()
    pipeline_args = Pipeline.call_args[0][0]
    assert mock_input in pipeline_args
    assert mock_stt in pipeline_args
    assert mock_generator in pipeline_args
    assert mock_tts in pipeline_args
    assert mock_output in pipeline_args


async def test_transport_params(
    pipecat_mocks, settings, fake_deps
) -> None:
    cli, store, ctx, openrouter = fake_deps

    LocalAudioTransportParams = pipecat_mocks["pipecat.transports.local.audio"].LocalAudioTransportParams
    SileroVADAnalyzer = pipecat_mocks["pipecat.audio.vad.silero"].SileroVADAnalyzer
    LocalSmartTurnAnalyzerV3 = pipecat_mocks[
        "pipecat.audio.turn.smart_turn.local_smart_turn_v3"
    ].LocalSmartTurnAnalyzerV3

    mock_vad = MagicMock(name="vad_instance")
    mock_turn = MagicMock(name="turn_instance")
    SileroVADAnalyzer.return_value = mock_vad
    LocalSmartTurnAnalyzerV3.return_value = mock_turn

    with patch("src.generator.create_generator_processor", return_value=MagicMock()), \
         patch("src.pipeline.create_edge_tts_service", return_value=MagicMock()):
        from src.pipeline import build_pipeline

        await build_pipeline(settings, cli, store, ctx, openrouter)

    LocalAudioTransportParams.assert_called_once_with(
        audio_in_enabled=True,
        audio_out_enabled=True,
        audio_in_sample_rate=16000,
        vad_analyzer=mock_vad,
        turn_analyzer=mock_turn,
    )


async def test_vad_uses_short_stop_secs(pipecat_mocks, settings, fake_deps) -> None:
    """VAD endpointing: 0.5s compromise between latency and Groq STT rate pressure."""
    cli, store, ctx, openrouter = fake_deps
    VADParams = pipecat_mocks["pipecat.audio.vad.vad_analyzer"].VADParams

    with patch("src.generator.create_generator_processor", return_value=MagicMock()), \
         patch("src.pipeline.create_edge_tts_service", return_value=MagicMock()):
        from src.pipeline import build_pipeline

        await build_pipeline(settings, cli, store, ctx, openrouter)

    VADParams.assert_called_once()
    kwargs = VADParams.call_args.kwargs
    assert kwargs["stop_secs"] == 0.5
    assert kwargs["start_secs"] == 0.3


async def test_smart_turn_uses_fallback_stop_secs(
    pipecat_mocks, settings, fake_deps
) -> None:
    """SmartTurnV3 must be the fallback safety net at stop_secs=1.0."""
    cli, store, ctx, openrouter = fake_deps
    SmartTurnParams = pipecat_mocks[
        "pipecat.audio.turn.smart_turn.base_smart_turn"
    ].SmartTurnParams
    LocalSmartTurnAnalyzerV3 = pipecat_mocks[
        "pipecat.audio.turn.smart_turn.local_smart_turn_v3"
    ].LocalSmartTurnAnalyzerV3

    with patch("src.generator.create_generator_processor", return_value=MagicMock()), \
         patch("src.pipeline.create_edge_tts_service", return_value=MagicMock()):
        from src.pipeline import build_pipeline

        await build_pipeline(settings, cli, store, ctx, openrouter)

    SmartTurnParams.assert_called_once()
    assert SmartTurnParams.call_args.kwargs["stop_secs"] == 1.0
    LocalSmartTurnAnalyzerV3.assert_called_once()
    assert "params" in LocalSmartTurnAnalyzerV3.call_args.kwargs


@pytest.mark.parametrize("lang_code", ["en", "uk"])
async def test_stt_language_enum_and_prob_metrics(
    pipecat_mocks, fake_deps, lang_code
) -> None:
    """STT must receive Language(groq_language) enum and include_prob_metrics=True."""
    s = Settings()
    s.groq_api_key = "test-key"
    s.groq_language = lang_code
    s.indication.enabled = False

    GroqSTTService = pipecat_mocks["pipecat.services.groq.stt"].GroqSTTService
    Language = pipecat_mocks["pipecat.transcriptions.language"].Language
    cli, store, ctx, openrouter = fake_deps

    with patch("src.generator.create_generator_processor", return_value=MagicMock()), \
         patch("src.pipeline.create_edge_tts_service", return_value=MagicMock()), \
         patch("src.tts_edge._build_edge_tts_class", return_value=MagicMock()):
        from src.pipeline import build_pipeline

        await build_pipeline(s, cli, store, ctx, openrouter)

    GroqSTTService.assert_called_once()
    kwargs = GroqSTTService.call_args.kwargs
    assert kwargs.get("include_prob_metrics") is True
    assert kwargs.get("language") == Language(lang_code)
