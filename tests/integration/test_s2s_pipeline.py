"""Integration tests for Phase-1 s2s-realtime pipeline wiring."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("pipecat.pipeline.pipeline")

from src.config import Mode, Settings  # noqa: E402
from src.context import ContextBuilder  # noqa: E402
from src.openrouter_cli import OpenRouterCLI  # noqa: E402
from src.pipeline import build_pipeline  # noqa: E402
from src.storage import TranscriptStore  # noqa: E402


@pytest.fixture
async def env(tmp_path):
    settings = Settings()
    settings.groq_api_key = "gsk_fake_for_test"
    settings.openrouter_api_key = "sk-or-fake-for-test"
    settings.mode = Mode.AMBIENT
    settings.speaker_id_enabled = False
    settings.db_path = tmp_path / "heare.db"
    settings.workspace_dir = tmp_path / "workspace"
    settings.log_dir = tmp_path / "logs"
    settings.workspace_dir.mkdir()
    settings.log_dir.mkdir()

    store = TranscriptStore(settings.db_path)
    await store.init()
    ctx = ContextBuilder(store, settings)
    try:
        yield settings, store, ctx
    finally:
        await store.close()


async def test_generator_mode_builds_generator_processor(env, monkeypatch):
    settings, store, ctx = env
    settings.generator_mode = True

    def _fake_transport(*a, **kw):
        t = MagicMock()
        t.input = MagicMock(return_value=MagicMock(name="input"))
        t.output = MagicMock(return_value=MagicMock(name="output"))
        return t

    import pipecat.transports.local.audio as audio_mod
    monkeypatch.setattr(audio_mod, "LocalAudioTransport", _fake_transport)

    openrouter = OpenRouterCLI(api_key="k", model=settings.openrouter_model)
    claude_cli = MagicMock()

    task, middle, tts_cache = await build_pipeline(
        settings=settings,
        claude_cli=claude_cli,
        store=store,
        context_builder=ctx,
        openrouter_cli=openrouter,
        persona="Ти Heare.",
    )

    # Middle element is a GeneratorProcessor, not a Decider
    assert type(middle).__name__ == "GeneratorProcessor"
    # Legacy shutdown contract preserved (both decider + generator expose it)
    assert callable(getattr(middle, "shutdown", None))
    assert callable(getattr(middle, "on_heartbeat_tick", None))


async def test_generator_mode_requires_openrouter_cli(env, monkeypatch):
    settings, store, ctx = env
    settings.generator_mode = True
    claude_cli = MagicMock()

    def _fake_transport(*a, **kw):
        t = MagicMock()
        t.input = MagicMock(return_value=MagicMock(name="input"))
        t.output = MagicMock(return_value=MagicMock(name="output"))
        return t

    monkeypatch.setattr("src.pipeline.LocalAudioTransport", _fake_transport, raising=False)
    import pipecat.transports.local.audio as audio_mod
    monkeypatch.setattr(audio_mod, "LocalAudioTransport", _fake_transport)

    with pytest.raises(RuntimeError, match="openrouter_cli"):
        await build_pipeline(
            settings=settings,
            claude_cli=claude_cli,
            store=store,
            context_builder=ctx,
            openrouter_cli=None,
        )
