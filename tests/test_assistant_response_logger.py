"""AssistantResponseProcessor — capture LLM text streaming to TTS and log
one row per response with speaker_id='bot'.

The processor sits BETWEEN the LLM service and the TTS service so it can
observe LLMFullResponseStartFrame / LLMTextFrame / LLMFullResponseEndFrame
before TTS consumes them. EdgeTTSService never emits TTSTextFrame, so the
capture must happen on the LLM side.
"""
from __future__ import annotations

import pytest


def _patch_push_frame(proc):
    captured: list = []

    async def fake_push(frame, direction=None):
        captured.append(frame)

    proc.push_frame = fake_push  # type: ignore[method-assign]
    return captured


class _StoreSpy:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def log_transcript(
        self,
        text: str,
        mode: str,
        speaker_id: str | None = None,
        speaker_confidence: float | None = None,
        audio_event_label: str | None = None,
        audio_event_score: float | None = None,
        agent_mode: str | None = None,
        agent_spoken: bool | None = None,
    ) -> int:
        self.calls.append(
            {
                "text": text,
                "mode": mode,
                "speaker_id": speaker_id,
                "agent_mode": agent_mode,
                "agent_spoken": agent_spoken,
            }
        )
        return len(self.calls)


@pytest.mark.asyncio
async def test_logger_aggregates_llm_text_frames_per_response():
    from pipecat.frames.frames import (
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
    )

    from src.pipeline.stages.assistant_response_logger import create_assistant_response_logger

    store = _StoreSpy()
    proc = create_assistant_response_logger(store=store)
    _patch_push_frame(proc)

    await proc.process_frame(LLMFullResponseStartFrame(), None)
    await proc.process_frame(LLMTextFrame("Hello "), None)
    await proc.process_frame(LLMTextFrame("there, "), None)
    await proc.process_frame(LLMTextFrame("friend."), None)
    await proc.process_frame(LLMFullResponseEndFrame(), None)

    assert len(store.calls) == 1
    call = store.calls[0]
    assert call["speaker_id"] == "bot"
    assert call["mode"] == "assistant"
    assert call["text"] == "Hello there, friend."


@pytest.mark.asyncio
async def test_logger_skips_text_outside_response_window():
    """Text frames seen outside an LLM response window must be ignored."""
    from pipecat.frames.frames import LLMFullResponseEndFrame, LLMTextFrame

    from src.pipeline.stages.assistant_response_logger import create_assistant_response_logger

    store = _StoreSpy()
    proc = create_assistant_response_logger(store=store)
    _patch_push_frame(proc)

    await proc.process_frame(LLMTextFrame("orphan"), None)
    await proc.process_frame(LLMFullResponseEndFrame(), None)

    assert store.calls == []


@pytest.mark.asyncio
async def test_logger_logs_one_row_per_response():
    from pipecat.frames.frames import (
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
    )

    from src.pipeline.stages.assistant_response_logger import create_assistant_response_logger

    store = _StoreSpy()
    proc = create_assistant_response_logger(store=store)
    _patch_push_frame(proc)

    # Response 1
    await proc.process_frame(LLMFullResponseStartFrame(), None)
    await proc.process_frame(LLMTextFrame("first"), None)
    await proc.process_frame(LLMFullResponseEndFrame(), None)
    # Response 2
    await proc.process_frame(LLMFullResponseStartFrame(), None)
    await proc.process_frame(LLMTextFrame("second"), None)
    await proc.process_frame(LLMFullResponseEndFrame(), None)

    assert [c["text"] for c in store.calls] == ["first", "second"]


@pytest.mark.asyncio
async def test_logger_logs_standalone_tts_speak_frame():
    """TTSSpeakFrame outside an LLM response is treated as a standalone
    utterance (e.g. startup greeting) and logged immediately."""
    from pipecat.frames.frames import TTSSpeakFrame

    from src.pipeline.stages.assistant_response_logger import create_assistant_response_logger

    store = _StoreSpy()
    proc = create_assistant_response_logger(store=store)
    _patch_push_frame(proc)

    await proc.process_frame(TTSSpeakFrame("heare online"), None)

    assert [c["text"] for c in store.calls] == ["heare online"]


@pytest.mark.asyncio
async def test_logger_passes_frames_through_downstream():
    from pipecat.frames.frames import (
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
    )

    from src.pipeline.stages.assistant_response_logger import create_assistant_response_logger

    proc = create_assistant_response_logger(store=None)
    captured = _patch_push_frame(proc)

    started = LLMFullResponseStartFrame()
    text = LLMTextFrame("hi")
    ended = LLMFullResponseEndFrame()

    await proc.process_frame(started, None)
    await proc.process_frame(text, None)
    await proc.process_frame(ended, None)

    assert captured == [started, text, ended]


@pytest.mark.asyncio
async def test_logger_tags_mode_and_spoken_from_session_state():
    """Text-response channel: each bot row carries the active mode and
    whether TTS spoke it (spoken = not profile.mute_output)."""
    from pipecat.frames.frames import (
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
    )

    from src.pipeline.language_state import LanguageState
    from src.pipeline.session_state import SessionState
    from src.pipeline.stages.assistant_response_logger import (
        create_assistant_response_logger,
    )

    # silent → muted → agent_spoken False
    store = _StoreSpy()
    ss = SessionState(LanguageState(), initial_mode="silent")
    proc = create_assistant_response_logger(store=store, session_state=ss)
    _patch_push_frame(proc)
    await proc.process_frame(LLMFullResponseStartFrame(), None)
    await proc.process_frame(LLMTextFrame("hidden words"), None)
    await proc.process_frame(LLMFullResponseEndFrame(), None)
    assert store.calls[-1]["agent_mode"] == "silent"
    assert store.calls[-1]["agent_spoken"] is False

    # ambient → not muted → agent_spoken True
    ss.set_mode("ambient")
    await proc.process_frame(LLMFullResponseStartFrame(), None)
    await proc.process_frame(LLMTextFrame("out loud"), None)
    await proc.process_frame(LLMFullResponseEndFrame(), None)
    assert store.calls[-1]["agent_mode"] == "ambient"
    assert store.calls[-1]["agent_spoken"] is True


@pytest.mark.asyncio
async def test_logger_no_session_state_leaves_tags_none():
    from pipecat.frames.frames import (
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
    )

    from src.pipeline.stages.assistant_response_logger import (
        create_assistant_response_logger,
    )

    store = _StoreSpy()
    proc = create_assistant_response_logger(store=store)
    _patch_push_frame(proc)
    await proc.process_frame(LLMFullResponseStartFrame(), None)
    await proc.process_frame(LLMTextFrame("hi"), None)
    await proc.process_frame(LLMFullResponseEndFrame(), None)
    assert store.calls[-1]["agent_mode"] is None
    assert store.calls[-1]["agent_spoken"] is None
