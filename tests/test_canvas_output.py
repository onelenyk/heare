"""Tests for canvas_output.py — CanvasOutputProcessor.

Covers HTML sanitisation, size truncation, mode-gating, and graceful
degradation when the store is unavailable.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pipecat.frames.frames import Frame

from src.pipeline.stages.canvas_output import MAX_SIZE, create_canvas_output
from src.pipeline.stages.output_router import CanvasContentFrame


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------


class MockProfile:
    """Minimal ModeProfile stand-in for testing mode-gating."""

    def __init__(self, outputs: frozenset[str], name: str = "test") -> None:
        self.outputs = outputs
        self.name = name
        self.voice_muted: bool = False


class MockSessionState:
    """Minimal SessionState stand-in exposing .profile."""

    def __init__(self, profile: MockProfile) -> None:
        self.profile = profile


@pytest.fixture
def mock_store() -> MagicMock:
    """TranscriptStore mock with AsyncMock for insert_display."""
    store = MagicMock()
    store.insert_display = AsyncMock()
    return store


@pytest.fixture
def make_processor(mock_store: MagicMock):
    """Factory returning a CanvasOutputProcessor wired to the mock store."""

    def _make(*, store=None, session_state=None):
        return create_canvas_output(
            store=store if store is not None else mock_store,
            session_state=session_state,
        )

    return _make


async def _push_canvas(proc, text: str) -> list[Frame]:
    """Push a CanvasContentFrame through *proc* and return captured frames."""
    captured: list[Frame] = []

    async def fake_push(frame, direction=None):
        captured.append(frame)

    proc.push_frame = fake_push  # type: ignore[method-assign]
    frame = CanvasContentFrame(text=text)
    await proc.process_frame(frame, None)
    return captured


# ---------------------------------------------------------------------------
#  Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_html_written_to_displays(
    make_processor, mock_store: MagicMock
) -> None:
    """Valid HTML is persisted to the displays table."""
    proc = make_processor()
    html = "<div>Hello World</div>"

    await _push_canvas(proc, html)

    mock_store.insert_display.assert_awaited_once_with(
        content_type="canvas/html", content=html
    )


@pytest.mark.asyncio
async def test_external_script_src_stripped(
    make_processor, mock_store: MagicMock
) -> None:
    """<script src='https://…'> tags are removed by sanitisation."""
    proc = make_processor()
    html = (
        "<div>Before</div>"
        '<script src="https://evil.com/x.js">doBad()</script>'
        "<div>After</div>"
    )

    await _push_canvas(proc, html)

    content = mock_store.insert_display.call_args[1]["content"]
    assert "https://evil.com" not in content
    # The opening tag should be gone; closing </script> may remain.
    assert '<script src="https' not in content
    assert "<div>Before</div>" in content
    assert "<div>After</div>" in content


@pytest.mark.asyncio
async def test_external_img_src_stripped(
    make_processor, mock_store: MagicMock
) -> None:
    """<img src='https://…'> tags are removed by sanitisation."""
    proc = make_processor()
    html = (
        "<div>X</div>"
        '<img src="https://tracker.com/pixel.png">'
        "<div>Y</div>"
    )

    await _push_canvas(proc, html)

    content = mock_store.insert_display.call_args[1]["content"]
    assert "https://tracker.com" not in content
    assert '<img src="https' not in content
    assert "<div>X</div>" in content
    assert "<div>Y</div>" in content


@pytest.mark.asyncio
async def test_content_truncated_at_max_size(
    make_processor, mock_store: MagicMock
) -> None:
    """Content exceeding MAX_SIZE (64 KB) is truncated."""
    proc = make_processor()
    huge = "x" * (MAX_SIZE + 1000)

    await _push_canvas(proc, huge)

    content = mock_store.insert_display.call_args[1]["content"]
    assert len(content) == MAX_SIZE
    assert content == "x" * MAX_SIZE


@pytest.mark.asyncio
async def test_canvas_blocked_when_not_in_outputs(
    make_processor, mock_store: MagicMock
) -> None:
    """In meeting mode (outputs = {"text"}), canvas writes are skipped."""
    meeting_profile = MockProfile(outputs=frozenset({"text"}), name="meeting")
    session_state = MockSessionState(meeting_profile)
    proc = make_processor(session_state=session_state)
    html = "<div>Should not persist</div>"

    frames = await _push_canvas(proc, html)

    mock_store.insert_display.assert_not_called()
    # Frame must still be pushed through — it is not dropped, just not persisted.
    assert len(frames) == 1
    assert isinstance(frames[0], CanvasContentFrame)
    assert frames[0].text == html


@pytest.mark.asyncio
async def test_no_store_skips_write_without_crashing(make_processor) -> None:
    """When store is None the processor skips the write and pushes through."""
    proc = make_processor(store=None)
    html = "<div>Hello</div>"

    frames = await _push_canvas(proc, html)

    assert len(frames) == 1
    assert isinstance(frames[0], CanvasContentFrame)
    assert frames[0].text == html


@pytest.mark.asyncio
async def test_non_canvas_frame_passes_through(
    make_processor, mock_store: MagicMock
) -> None:
    """Non-CanvasContentFrame frames are passed through untouched."""
    from pipecat.frames.frames import TTSSpeakFrame

    proc = make_processor()
    captured: list[Frame] = []

    async def fake_push(frame, direction=None):
        captured.append(frame)

    proc.push_frame = fake_push  # type: ignore[method-assign]
    non_canvas = TTSSpeakFrame(text="hello")
    await proc.process_frame(non_canvas, None)

    assert captured == [non_canvas]
    mock_store.insert_display.assert_not_called()


@pytest.mark.asyncio
async def test_canvas_allowed_in_silent_mode(
    make_processor, mock_store: MagicMock
) -> None:
    """Silent mode has 'canvas' in outputs, so canvas persists."""
    silent_profile = MockProfile(
        outputs=frozenset({"text", "canvas"}), name="silent"
    )
    session_state = MockSessionState(silent_profile)
    proc = make_processor(session_state=session_state)
    html = "<div>Persist me</div>"

    await _push_canvas(proc, html)

    mock_store.insert_display.assert_awaited_once_with(
        content_type="canvas/html", content=html
    )
