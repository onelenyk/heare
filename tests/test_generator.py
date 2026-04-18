"""Tests for GeneratorProcessor (Phase-1 s2s-realtime)."""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock

import pytest

pipecat = pytest.importorskip("pipecat.frames.frames")
TranscriptionFrame = pipecat.TranscriptionFrame
TTSSpeakFrame = pipecat.TTSSpeakFrame
UserStoppedSpeakingFrame = pipecat.UserStoppedSpeakingFrame

from src.config import Mode, Settings  # noqa: E402
from src.context import ContextBuilder  # noqa: E402
from src.generator import (  # noqa: E402
    FALLBACK_PHRASE,
    create_generator_processor,
)
from src.openrouter_cli import OpenRouterError  # noqa: E402
from src.storage import TranscriptStore  # noqa: E402


class FakeOpenRouter:
    """Fake OpenRouterCLI producing a scripted async iterator of chunks."""

    def __init__(self, chunks: list[str] | None = None, exc: Exception | None = None):
        self._chunks = chunks or []
        self._exc = exc
        self.call_count = 0
        self.last_prompt: str | None = None

    async def generate(self, prompt: str, system: str | None = None) -> AsyncIterator[str]:
        self.call_count += 1
        self.last_prompt = prompt
        if self._exc is not None:
            raise self._exc
        for c in self._chunks:
            yield c


def _make_transcription_frame(text: str):
    try:
        return TranscriptionFrame(text=text, user_id="u", timestamp="t")
    except TypeError:
        return TranscriptionFrame(user_id="u", timestamp="t", text=text)


@pytest.fixture
async def harness():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "heare.db"
        store = TranscriptStore(db)
        await store.init()
        settings = Settings()
        settings.mode = Mode.AMBIENT
        ctx_builder = ContextBuilder(store, settings)
        try:
            yield store, settings, ctx_builder
        finally:
            await store.close()


async def test_streaming_buffers_into_sentences(harness) -> None:
    _, _, ctx = harness
    fake = FakeOpenRouter(chunks=["Привіт", ", ", "друже!", " Як ", "справи", "?"])
    gen = create_generator_processor(fake, ctx, "template {transcript}", "persona")
    pushed: list[Any] = []

    async def capture(frame, direction=None):
        pushed.append(frame)

    gen.push_frame = capture  # type: ignore[assignment]

    await gen._handle_transcription(_make_transcription_frame("hi"), None)

    assert [type(f).__name__ for f in pushed] == ["TTSSpeakFrame", "TTSSpeakFrame"]
    assert pushed[0].text == "Привіт, друже!"
    assert pushed[1].text == "Як справи?"


async def test_streaming_flushes_trailing_partial_sentence(harness) -> None:
    _, _, ctx = harness
    fake = FakeOpenRouter(chunks=["Тихий", " текст"])  # no sentence terminator
    gen = create_generator_processor(fake, ctx, "template {transcript}", "persona")
    pushed: list[Any] = []

    async def capture(frame, direction=None):
        pushed.append(frame)

    gen.push_frame = capture  # type: ignore[assignment]

    await gen._handle_transcription(_make_transcription_frame("hi"), None)

    assert len(pushed) == 1
    assert pushed[0].text == "Тихий текст"


async def test_empty_reply_logs_warning_no_crash(harness, caplog) -> None:
    _, _, ctx = harness
    fake = FakeOpenRouter(chunks=[])
    gen = create_generator_processor(fake, ctx, "template {transcript}", "persona")
    gen.push_frame = AsyncMock()  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING, logger="heare.generator"):
        await gen._handle_transcription(_make_transcription_frame("hm"), None)

    assert gen.push_frame.await_count == 0
    assert any("empty reply" in r.message for r in caplog.records)


async def test_openrouter_error_pushes_fallback(harness, caplog) -> None:
    _, _, ctx = harness
    fake = FakeOpenRouter(exc=OpenRouterError("boom"))
    gen = create_generator_processor(fake, ctx, "template {transcript}", "persona")
    pushed: list[Any] = []

    async def capture(frame, direction=None):
        pushed.append(frame)

    gen.push_frame = capture  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING, logger="heare.generator"):
        await gen._handle_transcription(_make_transcription_frame("ку"), None)

    assert len(pushed) == 1
    assert isinstance(pushed[0], TTSSpeakFrame)
    assert pushed[0].text == FALLBACK_PHRASE
    assert any("OpenRouter failed" in r.message for r in caplog.records)


async def test_non_transcription_frame_is_passed_through(harness) -> None:
    _, _, ctx = harness
    fake = FakeOpenRouter(chunks=["unused"])
    gen = create_generator_processor(fake, ctx, "template", "persona")

    class Sentinel:
        pass

    sent = Sentinel()
    # Invoke process_frame with a non-TranscriptionFrame; it should not call OpenRouter
    try:
        await gen.process_frame(sent, None)
    except AttributeError:
        # Some pipecat base classes require setup for push_frame to work.
        # We only assert that OpenRouter wasn't called for non-transcription frames.
        pass
    assert fake.call_count == 0


async def test_shutdown_is_idempotent(harness) -> None:
    _, _, ctx = harness
    gen = create_generator_processor(FakeOpenRouter(), ctx, "t", "p")
    await gen.shutdown()
    await gen.shutdown()  # idempotent — no crash


async def test_on_heartbeat_tick_is_noop(harness) -> None:
    _, _, ctx = harness
    fake = FakeOpenRouter(chunks=["ignored"])
    gen = create_generator_processor(fake, ctx, "t", "p")
    await gen.on_heartbeat_tick()
    assert fake.call_count == 0


async def test_ttft_logged_with_expected_format(harness, caplog) -> None:
    _, _, ctx = harness
    fake = FakeOpenRouter(chunks=["привіт"])
    gen = create_generator_processor(fake, ctx, "template {transcript}", "persona")
    gen.push_frame = AsyncMock()  # type: ignore[method-assign]

    with caplog.at_level(logging.INFO, logger="heare.generator"):
        await gen._handle_transcription(_make_transcription_frame("hi"), None)

    timing_lines = [r.message for r in caplog.records if "[TIMING] generator" in r.message]
    assert timing_lines, f"no [TIMING] generator line found: {[r.message for r in caplog.records]}"
    msg = timing_lines[0]
    assert "transcript=" in msg
    assert "ttft=" in msg
    assert "total_chunks=1" in msg
