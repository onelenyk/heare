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
    assert "chunks=1" in msg
    assert "intents=0" in msg


# ---------- Phase 2.1 tests: intent emission + cancel keyword ----------


async def test_intent_emission_and_tts_separation(harness) -> None:
    """Generator pushes reply text to TTS and submits intent to queue — no tag leakage."""
    from src.actions import IntentQueue

    _, _, ctx = harness
    fake = FakeOpenRouter(
        chunks=[
            "Виконаю. ",
            '<intent>{"tool":"bash","args":"echo hi"}</intent>',
        ]
    )
    queue = IntentQueue()
    gen = create_generator_processor(
        fake, ctx, "template {transcript}", "persona", intent_queue=queue
    )
    pushed: list = []

    async def capture(frame, direction=None):
        pushed.append(frame)

    gen.push_frame = capture  # type: ignore[assignment]
    await gen._handle_transcription(_make_transcription_frame("запусти echo hi"), None)

    # TTS got "Виконаю." exactly; no '<' anywhere in emitted text
    tts_texts = [f.text for f in pushed]
    assert any("Виконаю" in t for t in tts_texts)
    for t in tts_texts:
        assert "<" not in t
    # Queue has the intent
    assert queue.pending_count() == 1


async def test_mid_stream_tag_no_leakage(harness) -> None:
    """Anti-leakage: tag split across chunks mid-stream — no `<` in TTS."""
    from src.actions import IntentQueue

    _, _, ctx = harness
    fake = FakeOpenRouter(
        chunks=[
            "Додам. ",
            '<intent>{"tool":"bash",',
            '"args":"x"}</intent>',
            " далі щось.",
        ]
    )
    queue = IntentQueue()
    gen = create_generator_processor(
        fake, ctx, "tpl {transcript}", "persona", intent_queue=queue
    )
    pushed: list = []

    async def capture(frame, direction=None):
        pushed.append(frame)

    gen.push_frame = capture  # type: ignore[assignment]
    await gen._handle_transcription(_make_transcription_frame("зроби"), None)

    tts_texts = [f.text for f in pushed]
    joined = " ".join(tts_texts)
    assert "<" not in joined
    assert "Додам." in joined
    assert "далі щось." in joined
    assert queue.pending_count() == 1


async def test_cancel_keyword_pops_pending_intent(harness, caplog) -> None:
    """User says "скасуй" → cancel_latest called, pending intent removed."""
    from src.actions import IntentQueue

    _, _, ctx = harness
    fake = FakeOpenRouter(chunks=["Скасовую."])
    queue = IntentQueue()
    await queue.submit({"tool": "bash", "args": "will-be-cancelled"})

    gen = create_generator_processor(
        fake, ctx, "tpl {transcript}", "persona", intent_queue=queue
    )
    gen.push_frame = AsyncMock()  # type: ignore[method-assign]

    with caplog.at_level(logging.INFO, logger="heare.generator"):
        await gen._handle_transcription(_make_transcription_frame("скасуй"), None)

    assert queue.pending_count() == 0
    assert any("INTENT CANCELLED" in r.message for r in caplog.records)


async def test_cancel_keyword_on_empty_queue_graceful(harness) -> None:
    """User says "скасуй" with empty queue → no crash, reply still speaks."""
    from src.actions import IntentQueue

    _, _, ctx = harness
    fake = FakeOpenRouter(chunks=["Немає чого скасовувати."])
    queue = IntentQueue()
    gen = create_generator_processor(
        fake, ctx, "tpl {transcript}", "persona", intent_queue=queue
    )
    pushed: list = []

    async def capture(frame, direction=None):
        pushed.append(frame)

    gen.push_frame = capture  # type: ignore[assignment]
    await gen._handle_transcription(_make_transcription_frame("скасуй"), None)
    assert queue.pending_count() == 0
    # Bot still replied
    assert any("Немає" in f.text for f in pushed)


async def test_cancel_keyword_negative_cases(harness) -> None:
    """Substring 'стоп' must NOT trigger cancel. 'скаси' (diff stem) must NOT trigger."""
    from src.actions import IntentQueue

    _, _, ctx = harness
    for phrase in ["стоп-кадр", "автостоп", "скаси мене"]:
        queue = IntentQueue()
        await queue.submit({"tool": "bash", "args": "stay-safe"})
        fake = FakeOpenRouter(chunks=["ok"])
        gen = create_generator_processor(
            fake, ctx, "tpl {transcript}", "persona", intent_queue=queue
        )
        gen.push_frame = AsyncMock()  # type: ignore[method-assign]
        await gen._handle_transcription(_make_transcription_frame(phrase), None)
        # Intent should still be there — no cancel triggered
        assert queue.pending_count() == 1, (
            f"phrase {phrase!r} unexpectedly cancelled the pending intent"
        )


async def test_tts_scrubber_removes_tool_names() -> None:
    """Phase 2.2 US-P2.2-07: `bash` as a standalone token gets stripped."""
    from src.generator import _scrub_tts_text

    out = _scrub_tts_text("Добре, запустив bash echo hi.")
    assert "bash" not in out.lower()
    assert "запустив" in out
    assert "echo hi" in out


async def test_tts_scrubber_handles_json_fragment_leak() -> None:
    """JSON fragment that leaked from an intent tag is stripped entirely."""
    from src.generator import _scrub_tts_text

    out = _scrub_tts_text(
        'Зроблю зараз {"tool":"bash","args":"x"} далі.'
    )
    assert '"tool"' not in out
    assert '"args"' not in out
    assert '{' not in out
    assert "Зроблю зараз" in out
    assert "далі." in out


async def test_tts_scrubber_bashful_passthrough() -> None:
    """Unrelated words containing 'bash' as a substring must NOT be stripped."""
    from src.generator import _scrub_tts_text

    out = _scrub_tts_text("Він був дуже bashful сьогодні.")
    assert "bashful" in out


async def test_intent_emission_under_saturated_context(harness) -> None:
    """Phase 2.2 US-P2.2-07b: intent tag still parses when prompt is saturated
    with conversation context."""
    from src.actions import IntentQueue
    from src.conversation import ConversationManager
    from src.context import ContextBuilder
    from unittest.mock import MagicMock

    store, settings, _ = harness
    mgr = ConversationManager(store, MagicMock())
    # Saturate the action log with 5 entries
    for i in range(1, 6):
        mgr.record_action_pending(i, "bash", f"cmd{i} long argument line here")
    ctx = ContextBuilder(store, settings, conversation_manager=mgr)

    fake = FakeOpenRouter(
        chunks=[
            "Виконаю. ",
            '<intent>{"tool":"bash",',
            '"args":"echo saturated"}</intent>',
        ]
    )
    queue = IntentQueue()
    gen = create_generator_processor(
        fake, ctx, "tpl {transcript}", "persona", intent_queue=queue
    )
    pushed: list = []

    async def capture(frame, direction=None):
        pushed.append(frame)

    gen.push_frame = capture  # type: ignore[assignment]
    await gen._handle_transcription(_make_transcription_frame("запусти"), None)

    # Intent extracted cleanly
    assert queue.pending_count() == 1
    # No '<' in any TTS frame
    tts_texts = [f.text for f in pushed]
    for t in tts_texts:
        assert "<" not in t


async def test_cancel_keyword_positive_edge_cases(harness) -> None:
    """Real cancellation phrases must trigger cancel_latest."""
    from src.actions import IntentQueue

    _, _, ctx = harness
    for phrase in [
        "скасуй!",
        "ну скасуй",
        "скасуй, будь ласка",
        "відміни замовлення",
    ]:
        queue = IntentQueue()
        await queue.submit({"tool": "bash", "args": "will-cancel"})
        fake = FakeOpenRouter(chunks=["ok"])
        gen = create_generator_processor(
            fake, ctx, "tpl {transcript}", "persona", intent_queue=queue
        )
        gen.push_frame = AsyncMock()  # type: ignore[method-assign]
        await gen._handle_transcription(_make_transcription_frame(phrase), None)
        assert queue.pending_count() == 0, (
            f"phrase {phrase!r} should have cancelled but queue has "
            f"{queue.pending_count()} pending"
        )
