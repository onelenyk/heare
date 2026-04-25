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
    gen._active_lang = "uk"  # simulate Ukrainian already established

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


async def test_handle_transcription_drops_during_enrollment(harness, caplog, monkeypatch) -> None:
    """US-EG-2: generator drops transcript while enrollment is active."""
    _, _, ctx = harness
    fake = FakeOpenRouter(chunks=["should not be called"])
    gen = create_generator_processor(fake, ctx, "template {transcript}", "persona")
    gen.push_frame = AsyncMock()  # type: ignore[method-assign]

    class _FakeIndication:
        is_enrollment_active = True

    monkeypatch.setattr("src.indication.get_indication", lambda: _FakeIndication())

    with caplog.at_level(logging.DEBUG, logger="heare.generator"):
        await gen._handle_transcription(_make_transcription_frame("hello"), None)

    assert fake.call_count == 0, "OpenRouter must not be called during enrollment"
    assert gen.push_frame.await_count == 0, "No TTSSpeakFrame should be pushed during enrollment"
    assert any("enrollment" in r.message for r in caplog.records), (
        "Expected drop log line mentioning 'enrollment'"
    )


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


async def test_tts_scrubber_strips_bash_completed_marker() -> None:
    """Phase AH2-02: Claude Code's '(Bash completed with no output)' marker
    is dropped in one piece so TTS never says fragments like
    '( completed with no output)'."""
    from src.generator import _scrub_tts_text

    out = _scrub_tts_text("(Bash completed with no output)")
    # Must be fully gone — no leftover parens, no 'completed', no 'no output'.
    assert "(" not in out
    assert "Bash" not in out
    assert "completed" not in out
    assert "no output" not in out


async def test_tts_scrubber_strips_marker_inside_ukrainian_text() -> None:
    """Phase AH2-02: the marker disappears inside surrounding speech without
    mangling the words around it."""
    from src.generator import _scrub_tts_text

    out = _scrub_tts_text("Фуй! (Bash completed with no output) наступне.")
    assert "Bash" not in out
    assert "completed" not in out
    assert "Фуй!" in out
    assert "наступне" in out


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
        gen._active_lang = "uk"  # simulate Ukrainian already established
        await gen._handle_transcription(_make_transcription_frame(phrase), None)
        assert queue.pending_count() == 0, (
            f"phrase {phrase!r} should have cancelled but queue has "
            f"{queue.pending_count()} pending"
        )


# ---------------------------------------------------------------------------
# Phase B-0 persistence — generator writes decisions and forwards transcript_id
# ---------------------------------------------------------------------------


async def _fetch_decisions(store) -> list[dict]:
    cursor = await store.db.execute(
        "SELECT id, transcript_id, type, reply, intent, action_json FROM decisions"
        " ORDER BY id"
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": r[0],
            "transcript_id": r[1],
            "type": r[2],
            "reply": r[3],
            "intent": r[4],
            "action_json": r[5],
        }
        for r in rows
    ]


async def test_generator_persists_speak_decisions_with_transcript_id(harness) -> None:
    """US-B0-02: each TTS push yields a decisions row with type='speak' and transcript_id."""
    store, settings, ctx = harness
    fake = FakeOpenRouter(chunks=["Привіт", ", ", "друже!", " Як ", "справи", "?"])
    gen = create_generator_processor(
        fake, ctx, "tpl {transcript}", "persona", store=store, settings=settings
    )
    gen.push_frame = AsyncMock()  # type: ignore[method-assign]

    await gen._handle_transcription(_make_transcription_frame("hi"), None)

    decisions = await _fetch_decisions(store)
    speak_rows = [d for d in decisions if d["type"] == "speak"]
    assert len(speak_rows) == 2, f"expected 2 speak rows, got {len(speak_rows)}: {speak_rows}"
    assert speak_rows[0]["reply"] == "Привіт, друже!"
    assert speak_rows[1]["reply"] == "Як справи?"
    assert all(r["transcript_id"] == 1 for r in speak_rows), (
        f"transcript_id should be threaded, got {[r['transcript_id'] for r in speak_rows]}"
    )


async def test_generator_persists_act_decision_and_queues_decision_id(harness) -> None:
    """US-B0-02: an intent emission yields a type='act' decision and Intent.decision_id matches."""
    from src.actions import IntentQueue

    store, settings, ctx = harness
    fake = FakeOpenRouter(
        chunks=[
            "Виконаю. ",
            '<intent>{"tool":"bash","args":"echo hi"}</intent>',
        ]
    )
    queue = IntentQueue()
    gen = create_generator_processor(
        fake,
        ctx,
        "tpl {transcript}",
        "persona",
        store=store,
        settings=settings,
        intent_queue=queue,
    )
    gen.push_frame = AsyncMock()  # type: ignore[method-assign]

    await gen._handle_transcription(
        _make_transcription_frame("запусти echo hi"), None
    )

    decisions = await _fetch_decisions(store)
    act_rows = [d for d in decisions if d["type"] == "act"]
    assert len(act_rows) == 1
    act = act_rows[0]
    assert act["intent"] == "bash"
    assert act["transcript_id"] == 1
    # action_json must serialize both tool and args
    assert act["action_json"] is not None
    import json as _json

    payload = _json.loads(act["action_json"])
    assert payload == {"tool": "bash", "args": "echo hi"}

    # Intent on the queue carries the decision_id returned by log_decision
    assert queue.pending_count() == 1
    intent = await queue.next()
    assert intent.decision_id == act["id"]
    assert intent.transcript_id == 1


async def test_generator_without_store_does_not_raise(harness) -> None:
    """US-B0-02: legacy code path (no store) is still supported."""
    _, _, ctx = harness
    fake = FakeOpenRouter(chunks=["Ok."])
    gen = create_generator_processor(fake, ctx, "tpl {transcript}", "persona")
    gen.push_frame = AsyncMock()  # type: ignore[method-assign]
    await gen._handle_transcription(_make_transcription_frame("hi"), None)
    # no assertion; the test just verifies nothing raised


# ---------------------------------------------------------------------------
# Phase BP-02: STT debounce inside GeneratorProcessor
# ---------------------------------------------------------------------------


def _spy_handle_factory(real_handle, sink: list[str]):
    """Spy that records the effective transcript text passed to _handle_transcription.

    The debounce flush passes its combined string via ``override_text``; the
    inline path uses ``frame.text``. The spy captures whichever wins so each
    test sees the actual transcript that the production code would consume.
    """
    async def spy_handle(frame, direction, override_text=None):
        text = override_text if override_text is not None else (frame.text or "")
        sink.append(text)
        await real_handle(frame, direction, override_text=override_text)

    return spy_handle


async def test_debounce_coalesces_two_close_frames(harness) -> None:
    """Phase BP-02: two TranscriptionFrames within the debounce window
    produce exactly ONE _handle_transcription call with combined text."""
    import asyncio as _asyncio

    _, settings, ctx = harness
    settings.transcript_debounce_seconds = 0.1  # short window for the test
    fake = FakeOpenRouter(chunks=["Ok."])
    gen = create_generator_processor(
        fake, ctx, "tpl {transcript}", "persona", settings=settings
    )
    gen.push_frame = AsyncMock()  # type: ignore[method-assign]
    handled_calls: list[str] = []
    gen._handle_transcription = _spy_handle_factory(  # type: ignore[method-assign]
        gen._handle_transcription, handled_calls
    )

    # Two frames 30ms apart — well within the 100ms window
    await gen.process_frame(_make_transcription_frame("Перша частина."), None)
    await _asyncio.sleep(0.03)
    await gen.process_frame(_make_transcription_frame("друга частина."), None)
    # Wait for debounce to fire
    await _asyncio.sleep(0.2)

    assert len(handled_calls) == 1, (
        f"expected 1 combined call, got {len(handled_calls)}: {handled_calls}"
    )
    assert handled_calls[0] == "Перша частина. друга частина."


async def test_debounce_single_frame_processed_after_window(harness) -> None:
    """Phase BP-02: a single TranscriptionFrame is still processed (after
    the debounce window) — the debounce never silently drops input."""
    import asyncio as _asyncio

    _, settings, ctx = harness
    settings.transcript_debounce_seconds = 0.05
    fake = FakeOpenRouter(chunks=["Ok."])
    gen = create_generator_processor(
        fake, ctx, "tpl {transcript}", "persona", settings=settings
    )
    gen.push_frame = AsyncMock()  # type: ignore[method-assign]
    handled_calls: list[str] = []
    gen._handle_transcription = _spy_handle_factory(  # type: ignore[method-assign]
        gen._handle_transcription, handled_calls
    )

    await gen.process_frame(_make_transcription_frame("одне речення"), None)
    await _asyncio.sleep(0.15)
    assert handled_calls == ["одне речення"]


async def test_debounce_zero_disables_buffering(harness) -> None:
    """Phase BP-02: transcript_debounce_seconds=0 dispatches each frame
    immediately (legacy behaviour, used by older tests)."""
    _, settings, ctx = harness
    settings.transcript_debounce_seconds = 0.0
    fake = FakeOpenRouter(chunks=["Ok."])
    gen = create_generator_processor(
        fake, ctx, "tpl {transcript}", "persona", settings=settings
    )
    gen.push_frame = AsyncMock()  # type: ignore[method-assign]
    handled_calls: list[str] = []
    gen._handle_transcription = _spy_handle_factory(  # type: ignore[method-assign]
        gen._handle_transcription, handled_calls
    )

    await gen.process_frame(_make_transcription_frame("a"), None)
    await gen.process_frame(_make_transcription_frame("b"), None)
    # No sleep — both should already have been dispatched inline.
    assert handled_calls == ["a", "b"]


# ---------------------------------------------------------------------------
# US-I18N-05: TTS voice swap tests
# ---------------------------------------------------------------------------


def _make_transcription_frame_with_lang(text: str, lang_name: str):
    """Create a TranscriptionFrame with a mock result.language attribute."""
    from unittest.mock import MagicMock

    frame = _make_transcription_frame(text)
    result = MagicMock()
    result.language = lang_name
    frame.result = result
    return frame


class FakeTTSService:
    """Minimal TTS service stub for voice-swap tests."""

    def __init__(self, initial_voice: str = "en-US-AriaNeural"):
        self._voice = initial_voice
        self.set_voice_calls: list[str] = []

    def set_voice(self, voice: str) -> None:
        self._voice = voice
        self.set_voice_calls.append(voice)


async def test_tts_voice_swap_on_language_change(harness, caplog) -> None:
    """After 2 Ukrainian turns (hysteresis), set_voice called with Ukrainian voice."""
    _, settings, ctx = harness
    fake = FakeOpenRouter(chunks=["Ok."])
    tts = FakeTTSService(initial_voice="en-US-AriaNeural")
    settings.tts_voice = "en-US-AriaNeural"
    gen = create_generator_processor(
        fake, ctx, "tpl {transcript}", "persona", settings=settings, tts_service=tts
    )
    gen.push_frame = AsyncMock()  # type: ignore[method-assign]
    # Force active lang to English so we can test English→Ukrainian transition
    gen._active_lang = "en"
    gen._current_voice = "en-US-AriaNeural"

    with caplog.at_level(logging.INFO, logger="heare.generator"):
        # First Ukrainian turn — hysteresis pending, no swap yet
        await gen._handle_transcription(
            _make_transcription_frame_with_lang("привіт", "ukrainian"), None
        )
        assert tts.set_voice_calls == [], "no swap on first detection"

        # Second Ukrainian turn — hysteresis satisfied, swap fires
        await gen._handle_transcription(
            _make_transcription_frame_with_lang("як справи", "ukrainian"), None
        )

    assert "uk-UA-OstapNeural" in tts.set_voice_calls
    assert any("[TTS VOICE SWAP]" in r.message for r in caplog.records)
    swap_msg = next(r.message for r in caplog.records if "[TTS VOICE SWAP]" in r.message)
    assert "from=" in swap_msg
    assert "to=" in swap_msg

    # Now switch back to English (2 turns)
    tts.set_voice_calls.clear()
    await gen._handle_transcription(
        _make_transcription_frame_with_lang("hello", "english"), None
    )
    assert tts.set_voice_calls == [], "no swap on first English detection"
    await gen._handle_transcription(
        _make_transcription_frame_with_lang("how are you", "english"), None
    )
    assert "en-US-AriaNeural" in tts.set_voice_calls


async def test_tts_voice_no_swap_same_language(harness, caplog) -> None:
    """Two turns in the same language after it's active produce no set_voice call."""
    _, settings, ctx = harness
    fake = FakeOpenRouter(chunks=["Ok."])
    tts = FakeTTSService(initial_voice="uk-UA-OstapNeural")
    settings.tts_voice = "uk-UA-OstapNeural"
    gen = create_generator_processor(
        fake, ctx, "tpl {transcript}", "persona", settings=settings, tts_service=tts
    )
    gen.push_frame = AsyncMock()  # type: ignore[method-assign]
    # Force active lang to Ukrainian so same-language turns don't trigger swap
    gen._active_lang = "uk"
    gen._current_voice = "uk-UA-OstapNeural"

    with caplog.at_level(logging.INFO, logger="heare.generator"):
        await gen._handle_transcription(
            _make_transcription_frame_with_lang("привіт", "ukrainian"), None
        )
        await gen._handle_transcription(
            _make_transcription_frame_with_lang("як справи", "ukrainian"), None
        )

    assert tts.set_voice_calls == [], "no set_voice when language stays the same"
    assert not any("[TTS VOICE SWAP]" in r.message for r in caplog.records)


async def test_tts_voice_swap_without_service(harness) -> None:
    """tts_service=None path does not crash on language change."""
    _, settings, ctx = harness
    fake = FakeOpenRouter(chunks=["Ok."])
    gen = create_generator_processor(
        fake, ctx, "tpl {transcript}", "persona", settings=settings, tts_service=None
    )
    gen.push_frame = AsyncMock()  # type: ignore[method-assign]

    # Two Ukrainian turns to trigger hysteresis swap — must not raise
    await gen._handle_transcription(
        _make_transcription_frame_with_lang("привіт", "ukrainian"), None
    )
    await gen._handle_transcription(
        _make_transcription_frame_with_lang("як справи", "ukrainian"), None
    )


# ---------------------------------------------------------------------------
# US-I18N-03: cancel multilingual, lang propagation, hysteresis, TIMING, LANG_MISMATCH
# ---------------------------------------------------------------------------


async def test_cancel_keyword_english(harness) -> None:
    """English 'cancel' cancels a pending intent when active_lang is 'en'."""
    from src.actions import IntentQueue

    _, _, ctx = harness
    queue = IntentQueue()
    await queue.submit({"tool": "bash", "args": "will-be-cancelled"})
    fake = FakeOpenRouter(chunks=["Cancelled."])
    gen = create_generator_processor(
        fake, ctx, "tpl {transcript}", "persona", intent_queue=queue
    )
    gen.push_frame = AsyncMock()  # type: ignore[method-assign]
    gen._active_lang = "en"
    frame = _make_transcription_frame_with_lang("cancel", "english")
    await gen._handle_transcription(frame, None)
    assert queue.pending_count() == 0


async def test_cancel_keyword_russian(harness) -> None:
    """Russian 'отмени' cancels a pending intent when active_lang is 'ru'."""
    from src.actions import IntentQueue

    _, _, ctx = harness
    queue = IntentQueue()
    await queue.submit({"tool": "bash", "args": "will-be-cancelled"})
    fake = FakeOpenRouter(chunks=["Ok."])
    gen = create_generator_processor(
        fake, ctx, "tpl {transcript}", "persona", intent_queue=queue
    )
    gen.push_frame = AsyncMock()  # type: ignore[method-assign]
    gen._active_lang = "ru"
    frame = _make_transcription_frame_with_lang("отмени", "russian")
    await gen._handle_transcription(frame, None)
    assert queue.pending_count() == 0


async def test_language_propagated_to_context(harness) -> None:
    """user_language key appears in context dict passed to prompt template."""
    _, settings, ctx = harness
    captured_ctx: dict = {}

    original_build = ctx.build_for_generator

    async def capture_build(**kwargs):
        result = await original_build(**kwargs)
        captured_ctx.update(result)
        return result

    ctx.build_for_generator = capture_build  # type: ignore[method-assign]
    fake = FakeOpenRouter(chunks=["Ok."])
    gen = create_generator_processor(
        fake, ctx, "tpl {transcript}", "persona", settings=settings
    )
    gen.push_frame = AsyncMock()  # type: ignore[method-assign]
    gen._active_lang = "uk"
    await gen._handle_transcription(_make_transcription_frame("привіт"), None)
    assert "user_language" in captured_ctx
    assert captured_ctx["user_language"] == "Ukrainian"


async def test_hysteresis_single_detection_no_swap(harness) -> None:
    """Single non-current language detection does not switch active_lang."""
    _, settings, ctx = harness
    fake = FakeOpenRouter(chunks=["Ok."])
    gen = create_generator_processor(
        fake, ctx, "tpl {transcript}", "persona", settings=settings
    )
    gen.push_frame = AsyncMock()  # type: ignore[method-assign]
    gen._active_lang = "en"
    await gen._handle_transcription(
        _make_transcription_frame_with_lang("привіт", "ukrainian"), None
    )
    assert gen._active_lang == "en"


async def test_hysteresis_two_consecutive_detections_swap(harness) -> None:
    """Two consecutive non-current detections switch active_lang."""
    _, settings, ctx = harness
    fake = FakeOpenRouter(chunks=["Ok."])
    gen = create_generator_processor(
        fake, ctx, "tpl {transcript}", "persona", settings=settings
    )
    gen.push_frame = AsyncMock()  # type: ignore[method-assign]
    gen._active_lang = "en"
    await gen._handle_transcription(
        _make_transcription_frame_with_lang("привіт", "ukrainian"), None
    )
    assert gen._active_lang == "en"
    await gen._handle_transcription(
        _make_transcription_frame_with_lang("як справи", "ukrainian"), None
    )
    assert gen._active_lang == "uk"


async def test_lang_field_in_timing_log(harness, caplog) -> None:
    """[TIMING] log line contains lang= field."""
    _, settings, ctx = harness
    fake = FakeOpenRouter(chunks=["Ok."])
    gen = create_generator_processor(
        fake, ctx, "tpl {transcript}", "persona", settings=settings
    )
    gen.push_frame = AsyncMock()  # type: ignore[method-assign]
    gen._active_lang = "uk"
    with caplog.at_level(logging.INFO, logger="heare.generator"):
        await gen._handle_transcription(_make_transcription_frame("привіт"), None)
    timing_records = [r for r in caplog.records if "[TIMING]" in r.message]
    assert timing_records, "no [TIMING] log record found"
    assert "lang=" in timing_records[0].message


async def test_lang_mismatch_logging(harness, caplog) -> None:
    """[LANG_MISMATCH] WARNING is logged when Gemini replies in wrong script."""
    _, settings, ctx = harness
    # Active lang is Ukrainian (expects Cyrillic), but reply is Latin
    fake = FakeOpenRouter(chunks=["Hello world this is a long enough reply."])
    gen = create_generator_processor(
        fake, ctx, "tpl {transcript}", "persona", settings=settings
    )
    gen.push_frame = AsyncMock()  # type: ignore[method-assign]
    gen._active_lang = "uk"
    with caplog.at_level(logging.WARNING, logger="heare.generator"):
        await gen._handle_transcription(_make_transcription_frame("привіт"), None)
    mismatch_records = [r for r in caplog.records if "[LANG_MISMATCH]" in r.message]
    assert mismatch_records, "[LANG_MISMATCH] WARNING not logged"
