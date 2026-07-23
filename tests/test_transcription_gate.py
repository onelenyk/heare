"""Tests for TranscriptionGateProcessor (Phase 2 PH2-01)."""
from __future__ import annotations

import asyncio
import tempfile
import time
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

pipecat = pytest.importorskip("pipecat.frames.frames")
TranscriptionFrame = pipecat.TranscriptionFrame

from src.config import Mode, Settings  # noqa: E402
from src.pipeline.language_state import LanguageState  # noqa: E402
from src.store.storage import TranscriptStore  # noqa: E402
from src.pipeline.stages.transcription_gate import create_transcription_gate  # noqa: E402


def _make_transcription_frame(text: str, whisper_lang: str | None = None):
    """Build a TranscriptionFrame with optional Whisper-result language.

    ``detect_language_from_frame`` reads ``frame.result.language`` (the
    raw Whisper STT name, lowercase — "english"/"ukrainian"/"russian"),
    not ``frame.language`` (which is a pipecat Language enum). We use a
    SimpleNamespace stub for ``result`` so tests don't depend on the
    Whisper SDK.
    """
    try:
        f = TranscriptionFrame(text=text, user_id="u", timestamp="t")
    except TypeError:
        f = TranscriptionFrame(user_id="u", timestamp="t", text=text)
    if whisper_lang is not None:
        f.result = types.SimpleNamespace(language=whisper_lang)
    return f


@pytest.fixture
async def harness():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "heare.db"
        store = TranscriptStore(db)
        await store.init()
        settings = Settings()
        settings.mode = Mode.AMBIENT
        try:
            yield store, settings
        finally:
            await store.close()


def _capture_pushed(processor) -> list[Any]:
    pushed: list[Any] = []

    async def capture(frame, direction=None):
        pushed.append(frame)

    processor.push_frame = capture  # type: ignore[assignment]
    return pushed


# ---------------------------------------------------------------------------
# Bot-speaking / indication / cooldown guards


async def test_bot_speaking_guard_drops_transcript(harness) -> None:
    store, settings = harness
    settings.barge_in_enabled = False  # legacy drop-everything path
    gate = create_transcription_gate(store=store, settings=settings)
    pushed = _capture_pushed(gate)

    gate._bot_speaking = True
    await gate._handle_transcription(_make_transcription_frame("hello"), None)

    assert pushed == []


async def test_bot_speaking_clears_lets_transcript_through(harness) -> None:
    store, settings = harness
    gate = create_transcription_gate(store=store, settings=settings)
    pushed = _capture_pushed(gate)

    gate._bot_speaking = False
    gate._bot_cooldown_until = 0.0  # past
    await gate._handle_transcription(_make_transcription_frame("hello"), None)

    assert [type(f).__name__ for f in pushed] == ["TranscriptionFrame"]
    assert pushed[0].text == "hello"


async def test_cooldown_guard_drops_transcript_within_window(harness) -> None:
    store, settings = harness
    settings.barge_in_enabled = False  # legacy drop-everything path
    gate = create_transcription_gate(store=store, settings=settings)
    pushed = _capture_pushed(gate)

    gate._bot_speaking = False
    gate._bot_cooldown_until = time.monotonic() + 60.0  # well in the future
    await gate._handle_transcription(_make_transcription_frame("hello"), None)

    assert pushed == []


async def test_indication_speaking_guard_drops_transcript(harness) -> None:
    store, settings = harness
    gate = create_transcription_gate(store=store, settings=settings)
    pushed = _capture_pushed(gate)

    gate._indication_speaking = True
    await gate._handle_transcription(_make_transcription_frame("hello"), None)
    assert pushed == []

    gate._indication_speaking = False
    gate._bot_cooldown_until = 0.0
    await gate._handle_transcription(_make_transcription_frame("now ok"), None)
    assert [type(f).__name__ for f in pushed] == ["TranscriptionFrame"]
    assert pushed[0].text == "now ok"


# ---------------------------------------------------------------------------
# Language hysteresis (2-turn)


async def test_language_hysteresis_requires_two_consecutive_turns(
    harness,
) -> None:
    store, settings = harness
    settings.groq_language = "en"
    gate = create_transcription_gate(store=store, settings=settings)
    _capture_pushed(gate)

    assert gate.active_language == "en"

    # First Ukrainian utterance — pending_lang set, active still 'en'.
    await gate._handle_transcription(
        _make_transcription_frame("Привіт", whisper_lang="ukrainian"), None
    )
    assert gate.active_language == "en"
    assert gate._pending_lang == "uk"
    assert gate._pending_lang_count == 1

    # Second Ukrainian utterance — hysteresis confirms, active flips.
    await gate._handle_transcription(
        _make_transcription_frame("Як справи", whisper_lang="ukrainian"), None
    )
    assert gate.active_language == "uk"
    assert gate._pending_lang is None
    assert gate._pending_lang_count == 0


async def test_language_hysteresis_resets_when_lang_match(harness) -> None:
    store, settings = harness
    settings.groq_language = "en"
    gate = create_transcription_gate(store=store, settings=settings)
    _capture_pushed(gate)

    # One Ukrainian (pending) then one English (matches active) -> reset.
    await gate._handle_transcription(
        _make_transcription_frame("Привіт", whisper_lang="ukrainian"), None
    )
    await gate._handle_transcription(
        _make_transcription_frame("hello again", whisper_lang="english"), None
    )
    assert gate.active_language == "en"
    assert gate._pending_lang is None
    assert gate._pending_lang_count == 0


# ---------------------------------------------------------------------------
# Voice swap


async def test_voice_swap_fires_on_language_flip(harness) -> None:
    store, settings = harness
    settings.groq_language = "en"
    tts = MagicMock()
    gate = create_transcription_gate(
        store=store, settings=settings, tts_service=tts
    )
    _capture_pushed(gate)

    await gate._handle_transcription(
        _make_transcription_frame("Привіт", whisper_lang="ukrainian"), None
    )
    await gate._handle_transcription(
        _make_transcription_frame("Як справи", whisper_lang="ukrainian"), None
    )

    assert tts.set_voice.called, "expected tts.set_voice() to fire on flip"
    voice = tts.set_voice.call_args[0][0]
    assert voice == "uk-UA-OstapNeural", f"unexpected swap target {voice!r}"


async def test_voice_swap_does_not_fire_on_same_language(harness) -> None:
    store, settings = harness
    settings.groq_language = "en"
    tts = MagicMock()
    gate = create_transcription_gate(
        store=store, settings=settings, tts_service=tts
    )
    _capture_pushed(gate)

    await gate._handle_transcription(
        _make_transcription_frame("hello", whisper_lang="english"), None
    )
    await gate._handle_transcription(
        _make_transcription_frame("again", whisper_lang="english"), None
    )

    assert not tts.set_voice.called


# ---------------------------------------------------------------------------
# Debounce — exercises _schedule_transcription / _flush_debounced directly


async def test_debounce_coalesces_consecutive_frames(harness) -> None:
    store, settings = harness
    settings.transcript_debounce_seconds = 0.05
    gate = create_transcription_gate(store=store, settings=settings)
    pushed = _capture_pushed(gate)

    await gate._schedule_transcription(_make_transcription_frame("hello"), None)
    await asyncio.sleep(0.01)
    await gate._schedule_transcription(_make_transcription_frame("world"), None)

    # Wait long enough for the debounce flush.
    await asyncio.sleep(0.15)

    transcripts = [
        f.text for f in pushed if type(f).__name__ == "TranscriptionFrame"
    ]
    assert transcripts == ["hello world"], (
        f"expected coalesced 'hello world', got {transcripts!r}"
    )


async def test_debounce_disabled_when_zero(harness) -> None:
    store, settings = harness
    settings.transcript_debounce_seconds = 0.0
    gate = create_transcription_gate(store=store, settings=settings)
    pushed = _capture_pushed(gate)

    # Direct call — no debounce path.
    await gate._handle_transcription(_make_transcription_frame("hello world"), None)

    transcripts = [
        f.text for f in pushed if type(f).__name__ == "TranscriptionFrame"
    ]
    assert transcripts == ["hello world"]


# ---------------------------------------------------------------------------
# LanguageState integration (PH2-04)


async def test_language_state_seeded_at_construction(harness) -> None:
    store, settings = harness
    settings.groq_language = "ru"
    state = LanguageState(initial="en")
    gate = create_transcription_gate(
        store=store, settings=settings, language_state=state
    )
    _capture_pushed(gate)

    # Constructor seeds state to the gate's default (groq_language).
    assert state.language == "ru"
    assert gate.active_language == "ru"


async def test_language_state_updated_on_hysteresis_flip(harness) -> None:
    store, settings = harness
    settings.groq_language = "en"
    state = LanguageState(initial="en")
    seen: list[str] = []
    state.set_change_listener(seen.append)

    gate = create_transcription_gate(
        store=store, settings=settings, language_state=state
    )
    _capture_pushed(gate)

    # First UK utterance — pending; state stays at 'en'.
    await gate._handle_transcription(
        _make_transcription_frame("Привіт", whisper_lang="ukrainian"), None
    )
    assert state.language == "en"

    # Second UK — hysteresis flips; state must follow.
    await gate._handle_transcription(
        _make_transcription_frame("Як справи", whisper_lang="ukrainian"), None
    )
    assert state.language == "uk"

    # Listener must have fired exactly once for the en->uk flip
    # (constructor seed of 'en' over initial 'en' is a no-op).
    assert seen == ["uk"]


async def test_language_state_no_write_when_state_absent(harness) -> None:
    """Sanity: gate without a language_state must not crash on flip."""
    store, settings = harness
    settings.groq_language = "en"
    gate = create_transcription_gate(store=store, settings=settings)
    _capture_pushed(gate)

    await gate._handle_transcription(
        _make_transcription_frame("Привіт", whisper_lang="ukrainian"), None
    )
    await gate._handle_transcription(
        _make_transcription_frame("Як справи", whisper_lang="ukrainian"), None
    )
    assert gate.active_language == "uk"


# ---------------------------------------------------------------------------
# PH2-05: InterruptionFrame on cancel detection


async def test_cancel_pushes_interruption_frame(harness) -> None:
    """Native cancel path: 'stop' must push an InterruptionFrame."""
    pipecat_frames = pytest.importorskip("pipecat.frames.frames")

    store, settings = harness
    gate = create_transcription_gate(store=store, settings=settings)
    pushed_with_dir: list[tuple[Any, Any]] = []

    async def capture(frame, direction=None):
        pushed_with_dir.append((frame, direction))

    gate.push_frame = capture  # type: ignore[assignment]

    await gate._handle_transcription(_make_transcription_frame("stop"), None)

    interruption_frames = [
        (f, d)
        for (f, d) in pushed_with_dir
        if isinstance(f, pipecat_frames.InterruptionFrame)
    ]
    assert len(interruption_frames) == 1, (
        f"expected exactly 1 InterruptionFrame, got {len(interruption_frames)} "
        f"(pushed={pushed_with_dir!r})"
    )


async def test_cancel_does_not_push_transcript_downstream(harness) -> None:
    """A cancel utterance is not a new LLM request — drop the
    transcript after pushing InterruptionFrame so the user_aggregator
    does not start an LLM turn."""
    pipecat_frames = pytest.importorskip("pipecat.frames.frames")

    store, settings = harness
    gate = create_transcription_gate(store=store, settings=settings)
    pushed: list[Any] = _capture_pushed(gate)

    await gate._handle_transcription(_make_transcription_frame("stop"), None)

    # No TranscriptionFrame was forwarded after the cancel.
    transcripts = [
        f for f in pushed if isinstance(f, pipecat_frames.TranscriptionFrame)
    ]
    assert transcripts == [], (
        f"cancel utterance should not be forwarded to LLM: pushed={pushed!r}"
    )


async def test_non_cancel_does_not_push_interruption_frame(harness) -> None:
    """Sanity: a regular utterance must NOT trigger InterruptionFrame."""
    pipecat_frames = pytest.importorskip("pipecat.frames.frames")

    store, settings = harness
    gate = create_transcription_gate(store=store, settings=settings)
    pushed = _capture_pushed(gate)

    await gate._handle_transcription(
        _make_transcription_frame("hello world"), None
    )

    interruption_frames = [
        f for f in pushed if isinstance(f, pipecat_frames.InterruptionFrame)
    ]
    assert interruption_frames == []




# ---------------------------------------------------------------------------
# inject_user_text — the typed-text path
#
# Typed text skips _handle_transcription entirely (it has no audio to drive
# the turn machinery), so these assert that it still gets the parts of a user
# turn that are not about sound: persistence, voice, an event.


async def test_inject_pushes_append_frame_not_transcription(harness) -> None:
    """A TranscriptionFrame would be buffered by the user aggregator and only
    flushed on an audio-driven turn stop — i.e. never, for typed text."""
    pipecat_frames = pytest.importorskip("pipecat.frames.frames")

    store, settings = harness
    gate = create_transcription_gate(store=store, settings=settings)
    pushed = _capture_pushed(gate)

    await gate.inject_user_text("hello daemon")

    assert len(pushed) == 1
    frame = pushed[0]
    assert isinstance(frame, pipecat_frames.LLMMessagesAppendFrame)
    assert frame.messages == [{"role": "user", "content": "hello daemon"}]
    assert frame.run_llm is True


async def test_inject_logs_transcript_marked_typed(harness) -> None:
    store, settings = harness
    gate = create_transcription_gate(store=store, settings=settings)
    _capture_pushed(gate)

    await gate.inject_user_text("привіт з дашборду")

    cursor = await store.db.execute("SELECT text, mode, source FROM transcripts")
    rows = await cursor.fetchall()
    assert rows == [("привіт з дашборду", settings.mode.value, "typed")]


async def test_spoken_transcript_marked_voice(harness) -> None:
    """The counterpart: speech is logged with the same shape, other source."""
    store, settings = harness
    gate = create_transcription_gate(store=store, settings=settings)
    _capture_pushed(gate)
    gate._bot_speaking = False
    gate._bot_cooldown_until = 0.0

    await gate._handle_transcription(_make_transcription_frame("hello"), None)

    cursor = await store.db.execute("SELECT text, source FROM transcripts")
    assert await cursor.fetchall() == [("hello", "voice")]


async def test_inject_does_not_set_voice_from_input(harness) -> None:
    """Voice follows the REPLY, not the typed input — the agent may answer in
    a different language than the user typed. tts_scrub owns the choice from
    the assembled response; the gate must not pre-empt it from the input."""
    store, settings = harness
    tts = MagicMock()
    gate = create_transcription_gate(store=store, settings=settings, tts_service=tts)
    _capture_pushed(gate)
    gate._current_voice = "en-US-AriaNeural"

    await gate.inject_user_text("hello")  # English input...

    # ...must not force an English voice: the reply could be Ukrainian, and a
    # voice set here would make it silent.
    tts.set_voice.assert_not_called()


async def test_inject_leaves_active_lang_alone(harness) -> None:
    """Injected text must not move the conversation's active language — that
    tracks spoken input and drives the system prompt's user_language."""
    store, settings = harness
    tts = MagicMock()
    gate = create_transcription_gate(store=store, settings=settings, tts_service=tts)
    _capture_pushed(gate)
    gate._active_lang = "en"

    await gate.inject_user_text("Скільки буде два плюс два?")

    assert gate._active_lang == "en"


async def test_inject_ignores_blank_text(harness) -> None:
    store, settings = harness
    gate = create_transcription_gate(store=store, settings=settings)
    pushed = _capture_pushed(gate)

    await gate.inject_user_text("   ")

    assert pushed == []
    cursor = await store.db.execute("SELECT COUNT(*) FROM transcripts")
    assert (await cursor.fetchone())[0] == 0


async def test_inject_survives_store_failure(harness) -> None:
    """A message we cannot log is still a message we must deliver."""
    store, settings = harness
    gate = create_transcription_gate(store=store, settings=settings)
    pushed = _capture_pushed(gate)

    async def boom(*a, **kw):
        raise RuntimeError("db gone")

    store.log_transcript = boom  # type: ignore[assignment]

    await gate.inject_user_text("still deliver me")

    assert len(pushed) == 1
