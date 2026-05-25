"""Tests for open-mic barge-in in the TranscriptionGateProcessor.

While the bot is speaking, speech heard through the mic is either the
bot's own audio echoing back (drop) or a genuine human interruption
(stop the bot via an upstream InterruptionFrame, then drive a fresh
turn). See src/pipeline/bot_speech_state.py for the rationale.
"""
from __future__ import annotations

import tempfile
import time
import types
from pathlib import Path
from typing import Any

import pytest

pipecat = pytest.importorskip("pipecat.frames.frames")
TranscriptionFrame = pipecat.TranscriptionFrame
InterruptionFrame = pipecat.InterruptionFrame
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402

from src.config import Mode, Settings  # noqa: E402
from src.pipeline.bot_speech_state import BotSpeechState  # noqa: E402
from src.pipeline.stages.transcription_gate import (  # noqa: E402
    create_transcription_gate,
)
from src.store.storage import TranscriptStore  # noqa: E402


def _frame(text: str):
    try:
        f = TranscriptionFrame(text=text, user_id="u", timestamp="t")
    except TypeError:
        f = TranscriptionFrame(user_id="u", timestamp="t", text=text)
    f.result = types.SimpleNamespace(language="english")
    return f


@pytest.fixture
async def harness():
    with tempfile.TemporaryDirectory() as tmp:
        store = TranscriptStore(Path(tmp) / "heare.db")
        await store.init()
        settings = Settings()
        settings.mode = Mode.AMBIENT
        try:
            yield store, settings
        finally:
            await store.close()


def _capture(processor) -> list[tuple[Any, Any]]:
    pushed: list[tuple[Any, Any]] = []

    async def capture(frame, direction=None):
        pushed.append((frame, direction))

    processor.push_frame = capture  # type: ignore[assignment]
    return pushed


async def test_distinct_speech_interrupts_bot(harness) -> None:
    store, settings = harness
    bss = BotSpeechState()
    bss.set_text("the weather today is sunny and quite warm outside")
    gate = create_transcription_gate(
        store=store, settings=settings, bot_speech_state=bss
    )
    pushed = _capture(gate)

    gate._bot_speaking = True
    await gate._handle_transcription(
        _frame("what time is the meeting tomorrow"), None
    )

    kinds = [type(f).__name__ for f, _ in pushed]
    assert "InterruptionFrame" in kinds
    interruption = next(
        (f, d) for f, d in pushed if isinstance(f, InterruptionFrame)
    )
    assert interruption[1] == FrameDirection.DOWNSTREAM
    # The barge-in transcript still drives a fresh user turn.
    assert "TranscriptionFrame" in kinds


async def test_echo_of_bot_speech_is_dropped(harness) -> None:
    store, settings = harness
    bss = BotSpeechState()
    bss.set_text("the weather today is sunny and quite warm outside")
    gate = create_transcription_gate(
        store=store, settings=settings, bot_speech_state=bss
    )
    pushed = _capture(gate)

    gate._bot_speaking = True
    # Mic catches the bot's own words — every token is in bot_text.
    await gate._handle_transcription(
        _frame("the weather today is sunny"), None
    )

    assert pushed == []


async def test_short_utterance_while_bot_speaks_is_dropped(harness) -> None:
    store, settings = harness
    bss = BotSpeechState()
    bss.set_text("a long explanation about something entirely unrelated")
    gate = create_transcription_gate(
        store=store, settings=settings, bot_speech_state=bss
    )
    pushed = _capture(gate)

    gate._bot_speaking = True
    await gate._handle_transcription(_frame("ok"), None)

    assert pushed == []


async def test_barge_in_disabled_preserves_legacy_drop(harness) -> None:
    store, settings = harness
    settings.barge_in_enabled = False
    bss = BotSpeechState()
    bss.set_text("the weather today is sunny")
    gate = create_transcription_gate(
        store=store, settings=settings, bot_speech_state=bss
    )
    pushed = _capture(gate)

    gate._bot_speaking = True
    await gate._handle_transcription(
        _frame("what time is the meeting tomorrow"), None
    )

    assert pushed == []


async def test_cooldown_window_also_allows_barge_in(harness) -> None:
    store, settings = harness
    bss = BotSpeechState()
    bss.set_text("some earlier sentence the bot just finished saying")
    gate = create_transcription_gate(
        store=store, settings=settings, bot_speech_state=bss
    )
    pushed = _capture(gate)

    gate._bot_speaking = False
    gate._bot_cooldown_until = time.monotonic() + 60.0
    await gate._handle_transcription(
        _frame("completely different question about lunch plans"), None
    )

    kinds = [type(f).__name__ for f, _ in pushed]
    assert "InterruptionFrame" in kinds
    assert "TranscriptionFrame" in kinds


async def test_no_bot_text_treated_as_genuine_interruption(harness) -> None:
    store, settings = harness
    bss = BotSpeechState()  # empty — nothing to echo
    gate = create_transcription_gate(
        store=store, settings=settings, bot_speech_state=bss
    )
    pushed = _capture(gate)

    gate._bot_speaking = True
    await gate._handle_transcription(_frame("hello there agent"), None)

    kinds = [type(f).__name__ for f, _ in pushed]
    assert "InterruptionFrame" in kinds
