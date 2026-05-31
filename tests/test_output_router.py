"""Unit tests for output_router — tagged text parser and frame dispatch."""
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pipecat.frames.frames")
from pipecat.frames.frames import (  # noqa: E402
    LLMFullResponseEndFrame,
    LLMTextFrame,
)

from src.pipeline.stages.output_router import (  # noqa: E402
    CanvasContentFrame,
    TextContentFrame,
    VoiceContentFrame,
    create_output_router,
)


def _make_router():
    router = create_output_router()
    router.push_frame = AsyncMock()  # type: ignore[method-assign]
    return router


async def _send(router, frame: Any) -> None:
    await router.process_frame(frame, None)


def _pushed_frames(router):
    return [c.args[0] for c in router.push_frame.await_args_list]


@pytest.mark.asyncio
async def test_voice_tag_emits_voice_content_frame() -> None:
    """[voice]hello[/voice] → VoiceContentFrame emitted."""
    router = _make_router()
    await _send(router, LLMTextFrame(text="[voice]hello[/voice]"))
    await _send(router, LLMFullResponseEndFrame())

    frames = _pushed_frames(router)
    voice_frames = [f for f in frames if isinstance(f, VoiceContentFrame)]
    assert len(voice_frames) == 1
    assert voice_frames[0].text == "hello"


@pytest.mark.asyncio
async def test_text_tag_emits_text_content_frame() -> None:
    """[text]note[/text] → TextContentFrame emitted."""
    router = _make_router()
    await _send(router, LLMTextFrame(text="[text]note[/text]"))
    await _send(router, LLMFullResponseEndFrame())

    frames = _pushed_frames(router)
    text_frames = [f for f in frames if isinstance(f, TextContentFrame)]
    assert len(text_frames) == 1
    assert text_frames[0].text == "note"


@pytest.mark.asyncio
async def test_canvas_tag_emits_canvas_content_frame() -> None:
    """[canvas]<h1>x</h1>[/canvas] → CanvasContentFrame emitted."""
    router = _make_router()
    await _send(router, LLMTextFrame(text="[canvas]<h1>x</h1>[/canvas]"))
    await _send(router, LLMFullResponseEndFrame())

    frames = _pushed_frames(router)
    canvas_frames = [f for f in frames if isinstance(f, CanvasContentFrame)]
    assert len(canvas_frames) == 1
    assert canvas_frames[0].text == "<h1>x</h1>"


@pytest.mark.asyncio
async def test_untagged_text_falls_back_to_text_frame(caplog) -> None:
    """Untagged text 'hello' → TextContentFrame (fallback via flush)."""
    caplog.set_level(logging.WARNING, logger="heare.output_router")

    router = _make_router()
    await _send(router, LLMTextFrame(text="hello"))
    await _send(router, LLMFullResponseEndFrame())

    frames = _pushed_frames(router)
    text_frames = [f for f in frames if isinstance(f, TextContentFrame)]
    assert len(text_frames) == 1
    assert text_frames[0].text == "hello"

    assert not any(isinstance(f, VoiceContentFrame) for f in frames)
    assert not any(isinstance(f, CanvasContentFrame) for f in frames)

    assert any("Untagged" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_nested_tags_inner_wins_semantics() -> None:
    """[voice]a[text]b[/text]c[/voice] → a,c as voice, b as text (inner-wins)."""
    router = _make_router()
    await _send(router, LLMTextFrame(text="[voice]a[text]b[/text]c[/voice]"))
    await _send(router, LLMFullResponseEndFrame())

    frames = _pushed_frames(router)

    voice_frames = [f for f in frames if isinstance(f, VoiceContentFrame)]
    assert len(voice_frames) == 2
    assert voice_frames[0].text == "a"
    assert voice_frames[1].text == "c"

    text_frames = [f for f in frames if isinstance(f, TextContentFrame)]
    assert len(text_frames) == 1
    assert text_frames[0].text == "b"


@pytest.mark.asyncio
async def test_unknown_tag_emits_as_text_with_warning(caplog) -> None:
    """[bogus]x[/bogus] → TextContentFrame + warning."""
    caplog.set_level(logging.WARNING, logger="heare.output_router")

    router = _make_router()
    await _send(router, LLMTextFrame(text="[bogus]x[/bogus]"))
    await _send(router, LLMFullResponseEndFrame())

    frames = _pushed_frames(router)
    text_frames = [f for f in frames if isinstance(f, TextContentFrame)]
    assert len(text_frames) == 1
    assert "bogus" in text_frames[0].text
    assert "x" in text_frames[0].text

    assert any("Untagged" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_empty_tag_skipped() -> None:
    """[voice][/voice] → no frame emitted (empty content skipped)."""
    router = _make_router()
    await _send(router, LLMTextFrame(text="[voice][/voice]"))
    await _send(router, LLMFullResponseEndFrame())

    frames = _pushed_frames(router)

    voice_frames = [f for f in frames if isinstance(f, VoiceContentFrame)]
    text_frames = [f for f in frames if isinstance(f, TextContentFrame)]
    canvas_frames = [f for f in frames if isinstance(f, CanvasContentFrame)]

    assert len(voice_frames) == 0
    assert len(text_frames) == 0
    assert len(canvas_frames) == 0


@pytest.mark.asyncio
async def test_streaming_split_across_chunks() -> None:
    """Tag content split across multiple LLMTextFrames is reassembled."""
    router = _make_router()
    await _send(router, LLMTextFrame(text="[voice]hel"))
    await _send(router, LLMTextFrame(text="lo[/voice]"))
    await _send(router, LLMFullResponseEndFrame())

    frames = _pushed_frames(router)
    voice_frames = [f for f in frames if isinstance(f, VoiceContentFrame)]
    assert len(voice_frames) == 1
    assert voice_frames[0].text == "hello"


@pytest.mark.asyncio
async def test_untagged_text_before_tagged_emits_text_frame_with_warning(
    caplog,
) -> None:
    """Untagged text before a known tag emits TextContentFrame + warning."""
    caplog.set_level(logging.WARNING, logger="heare.output_router")

    router = _make_router()
    await _send(router, LLMTextFrame(text="intro [voice]hello[/voice]"))
    await _send(router, LLMFullResponseEndFrame())

    frames = _pushed_frames(router)

    text_frames = [f for f in frames if isinstance(f, TextContentFrame)]
    assert len(text_frames) >= 1
    assert any("intro" in f.text for f in text_frames)

    voice_frames = [f for f in frames if isinstance(f, VoiceContentFrame)]
    assert len(voice_frames) == 1
    assert voice_frames[0].text == "hello"

    assert any("Untagged" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_multiple_same_type_tags() -> None:
    """Multiple [text] blocks emit multiple TextContentFrames."""
    router = _make_router()
    await _send(router, LLMTextFrame(text="[text]first[/text][text]second[/text]"))
    await _send(router, LLMFullResponseEndFrame())

    frames = _pushed_frames(router)
    text_frames = [f for f in frames if isinstance(f, TextContentFrame)]
    assert len(text_frames) == 2
    assert text_frames[0].text == "first"
    assert text_frames[1].text == "second"


@pytest.mark.asyncio
async def test_non_llm_frames_pass_through() -> None:
    """Non-LLMTextFrame frames are passed through unchanged."""
    from pipecat.frames.frames import TextFrame

    router = _make_router()
    frame = TextFrame(text="passthrough")
    await _send(router, frame)

    frames = _pushed_frames(router)
    assert len(frames) == 1
    assert frames[0] is frame


@pytest.mark.asyncio
async def test_llm_full_response_end_passes_through() -> None:
    """LLMFullResponseEndFrame is forwarded downstream after flush."""
    router = _make_router()
    end_frame = LLMFullResponseEndFrame()
    await _send(router, LLMTextFrame(text="[text]done[/text]"))
    await _send(router, end_frame)

    frames = _pushed_frames(router)
    assert any(isinstance(f, TextContentFrame) for f in frames)
    assert any(f is end_frame for f in frames)
