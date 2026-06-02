"""Tests for the LLM-based echo classifier (EchoClassifier)."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from src.config import Settings
from src.pipeline.stages.echo_classifier import create_echo_classifier


def _capture(processor) -> list[tuple[Any, Any]]:
    """Replace processor.push_frame with a fake that records pushed frames."""
    frames: list[tuple[Any, Any]] = []

    async def _fake_push(frame, direction=None):
        frames.append((frame, direction))

    processor.push_frame = _fake_push  # type: ignore[assignment]
    return frames


# ---------------------------------------------------------------------------
# Test 1: echo dropped
# ---------------------------------------------------------------------------


async def test_echo_dropped_when_llm_says_echo():
    """Transcription frame is dropped when LLM classifies it as ECHO."""

    class FakeBotSpeech:
        text = "hello world"

    settings = Settings()
    processor = create_echo_classifier(bot_speech_state=FakeBotSpeech(), settings=settings)
    processor._bot_speaking = True
    processor._classify_echo = AsyncMock(return_value="ECHO")
    pushed = _capture(processor)

    frame = TranscriptionFrame(text="hello world", user_id="user", timestamp="2024-01-01")
    await processor.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert len(pushed) == 0
    assert processor._echo_dropped == 1
    assert processor._echo_passed == 0
    processor._classify_echo.assert_called_once()


# ---------------------------------------------------------------------------
# Test 2: interruption passed
# ---------------------------------------------------------------------------


async def test_interruption_passed_when_llm_says_interrupt():
    """Transcription frame is pushed when LLM classifies it as INTERRUPT."""

    class FakeBotSpeech:
        text = "some bot text"

    settings = Settings()
    processor = create_echo_classifier(bot_speech_state=FakeBotSpeech(), settings=settings)
    processor._bot_speaking = True
    processor._classify_echo = AsyncMock(return_value="INTERRUPT")
    pushed = _capture(processor)

    frame = TranscriptionFrame(text="stop right now", user_id="user", timestamp="2024-01-01")
    await processor.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert len(pushed) == 1
    assert pushed[0][0] is frame
    assert processor._echo_passed == 1
    assert processor._echo_dropped == 0
    processor._classify_echo.assert_called_once()


# ---------------------------------------------------------------------------
# Test 3: bot not speaking passthrough
# ---------------------------------------------------------------------------


async def test_passthrough_when_bot_not_speaking():
    """Transcription frame passes through when bot is not speaking."""
    settings = Settings()
    processor = create_echo_classifier(settings=settings)
    processor._bot_speaking = False
    processor._classify_echo = AsyncMock(return_value="ECHO")
    pushed = _capture(processor)

    frame = TranscriptionFrame(text="hello", user_id="user", timestamp="2024-01-01")
    await processor.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert len(pushed) == 1
    assert pushed[0][0] is frame
    processor._classify_echo.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4: bot text empty passthrough
# ---------------------------------------------------------------------------


async def test_passthrough_when_bot_text_empty():
    """Transcription frame passes through when bot text snapshot is empty."""

    class FakeBotSpeech:
        text = ""

    settings = Settings()
    processor = create_echo_classifier(bot_speech_state=FakeBotSpeech(), settings=settings)
    processor._bot_speaking = True
    processor._classify_echo = AsyncMock(return_value="ECHO")
    pushed = _capture(processor)

    frame = TranscriptionFrame(text="hello", user_id="user", timestamp="2024-01-01")
    await processor.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert len(pushed) == 1
    assert pushed[0][0] is frame
    processor._classify_echo.assert_not_called()


# ---------------------------------------------------------------------------
# Test 5: transcript empty passthrough
# ---------------------------------------------------------------------------


async def test_passthrough_when_transcript_empty():
    """Transcription frame passes through when transcript text is empty."""

    class FakeBotSpeech:
        text = "hello"

    settings = Settings()
    processor = create_echo_classifier(bot_speech_state=FakeBotSpeech(), settings=settings)
    processor._bot_speaking = True
    processor._classify_echo = AsyncMock(return_value="ECHO")
    pushed = _capture(processor)

    frame = TranscriptionFrame(text="", user_id="user", timestamp="2024-01-01")
    await processor.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert len(pushed) == 1
    assert pushed[0][0] is frame
    processor._classify_echo.assert_not_called()


# ---------------------------------------------------------------------------
# Test 6: non-TranscriptionFrame passthrough
# ---------------------------------------------------------------------------


async def test_passthrough_non_transcription_frame():
    """System frames (BotStartedSpeaking, BotStoppedSpeaking, etc.) pass through."""
    settings = Settings()
    processor = create_echo_classifier(settings=settings)
    processor._classify_echo = AsyncMock(return_value="ECHO")
    pushed = _capture(processor)

    frames = [
        BotStartedSpeakingFrame(),
        BotStoppedSpeakingFrame(),
        Frame(),
    ]
    for frame in frames:
        await processor.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert len(pushed) == 3
    for i, frame in enumerate(frames):
        assert pushed[i][0] is frame
    processor._classify_echo.assert_not_called()


# ---------------------------------------------------------------------------
# Test 7: settings is None passthrough
# ---------------------------------------------------------------------------


async def test_settings_none_passthrough():
    """Transcription frame passes through when settings is None."""

    class FakeBotSpeech:
        text = "hello"

    processor = create_echo_classifier(bot_speech_state=FakeBotSpeech(), settings=None)
    processor._bot_speaking = True
    processor._classify_echo = AsyncMock(return_value="ECHO")
    pushed = _capture(processor)

    frame = TranscriptionFrame(text="hello", user_id="user", timestamp="2024-01-01")
    await processor.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert len(pushed) == 1
    assert pushed[0][0] is frame
    processor._classify_echo.assert_not_called()


# ---------------------------------------------------------------------------
# Test 8: echo_classifier disabled passthrough
# ---------------------------------------------------------------------------


async def test_echo_classifier_disabled_passthrough():
    """Transcription frame passes through when echo_classifier_enabled is False."""
    settings = Settings()
    settings.echo_classifier_enabled = False

    class FakeBotSpeech:
        text = "hello"

    processor = create_echo_classifier(bot_speech_state=FakeBotSpeech(), settings=settings)
    processor._bot_speaking = True
    processor._classify_echo = AsyncMock(return_value="ECHO")
    pushed = _capture(processor)

    frame = TranscriptionFrame(text="hello", user_id="user", timestamp="2024-01-01")
    await processor.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert len(pushed) == 1
    assert pushed[0][0] is frame
    processor._classify_echo.assert_not_called()


# ---------------------------------------------------------------------------
# Test 9: LLM error passthrough
# ---------------------------------------------------------------------------


async def test_passthrough_on_classify_error():
    """Transcription frame passes through when _classify_echo raises an exception."""

    class FakeBotSpeech:
        text = "hello world"

    settings = Settings()
    processor = create_echo_classifier(bot_speech_state=FakeBotSpeech(), settings=settings)
    processor._bot_speaking = True
    processor._classify_echo = AsyncMock(side_effect=httpx.HTTPError("LLM failed"))
    pushed = _capture(processor)

    frame = TranscriptionFrame(text="hello", user_id="user", timestamp="2024-01-01")
    await processor.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert len(pushed) == 1
    assert pushed[0][0] is frame
    assert processor._echo_dropped == 0
    assert processor._echo_passed == 0  # exception path does not increment counters
    processor._classify_echo.assert_called_once()
