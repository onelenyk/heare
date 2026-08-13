"""Tests for TTSScrubProcessor — the Pipecat stage that mutates LLM text
in-flight so TTS never speaks tool-name-only utterances."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest


pytest.importorskip("pipecat.frames.frames")
from pipecat.frames.frames import (  # noqa: E402
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TextFrame,
    TTSSpeakFrame,
)

from src.pipeline.stages.tts_scrub_processor import create_tts_scrub_processor  # noqa: E402


def _make_processor():
    proc = create_tts_scrub_processor()
    proc.push_frame = AsyncMock()  # type: ignore[method-assign]
    return proc


async def _send(proc, frame: Any) -> None:
    await proc.process_frame(frame, None)


def _pushed_frames(proc):
    return [c.args[0] for c in proc.push_frame.await_args_list]


@pytest.mark.asyncio
async def test_single_chunk_tool_name_blanked() -> None:
    """A single LLMTextFrame whose entire content is a tool name must be
    forwarded with empty text — not dropped entirely (TTS still needs
    LLMFullResponseEndFrame to close the response cycle)."""
    proc = _make_processor()
    await _send(proc, LLMFullResponseStartFrame())
    await _send(proc, LLMTextFrame(text="list_tools"))
    await _send(proc, LLMFullResponseEndFrame())

    frames = _pushed_frames(proc)
    text_frames = [f for f in frames if isinstance(f, LLMTextFrame)]
    assert len(text_frames) == 1
    assert text_frames[0].text == ""


@pytest.mark.asyncio
async def test_chunked_tool_name_blanked_via_buffer() -> None:
    """If the model streams ``list`` then ``_tools`` as separate chunks,
    each chunk passes the per-frame check, but the joined text is just
    a tool name — the buffer-level check must catch it and silence the
    rest of the response."""
    proc = _make_processor()
    await _send(proc, LLMFullResponseStartFrame())
    await _send(proc, LLMTextFrame(text="list"))
    await _send(proc, LLMTextFrame(text="_tools"))
    await _send(proc, LLMFullResponseEndFrame())

    frames = _pushed_frames(proc)
    text_frames = [f for f in frames if isinstance(f, LLMTextFrame)]
    # Both chunks forwarded, but the second blanked by the buffer rule.
    assert len(text_frames) == 2
    # The combined output must be empty so TTS speaks nothing.
    assert "".join(f.text for f in text_frames).strip() == ""


@pytest.mark.asyncio
async def test_streamed_chunks_keep_their_spaces() -> None:
    """The word gaps must survive a streamed response.

    A chunk is a token, not a sentence — DeepSeek streams
    ``['С', 'проб', 'ую', ' ще', ' раз']``. Scrubbing each chunk on its
    own ran ``.strip()`` on every one of them and ate the leading space,
    so TTS spoke ``Спробующераз``. Observed in production for 26% of all
    replies before this was fixed.
    """
    proc = _make_processor()
    deltas = ["Спроб", "ую", " ще", " раз", ",", " просто", " чита", "ю", " файл", "."]
    await _send(proc, LLMFullResponseStartFrame())
    for d in deltas:
        await _send(proc, LLMTextFrame(text=d))
    await _send(proc, LLMFullResponseEndFrame())

    text_frames = [f for f in _pushed_frames(proc) if isinstance(f, LLMTextFrame)]
    assert len(text_frames) == len(deltas)
    assert "".join(f.text for f in text_frames) == "Спробую ще раз, просто читаю файл."


@pytest.mark.asyncio
async def test_narration_split_across_chunks_is_stripped() -> None:
    """Narration the model streams across chunk boundaries is caught.

    ``bash: system_profiler`` arriving as three chunks matches nothing
    per-chunk; only the joined text does. The surrounding sentence must
    survive with its spacing intact.
    """
    proc = _make_processor()
    deltas = ["Перевіряю. ", "bash", ": system_pro", "filer SPAudioDataType.", " Готово."]
    await _send(proc, LLMFullResponseStartFrame())
    for d in deltas:
        await _send(proc, LLMTextFrame(text=d))
    await _send(proc, LLMFullResponseEndFrame())

    text_frames = [f for f in _pushed_frames(proc) if isinstance(f, LLMTextFrame)]
    assert len(text_frames) == len(deltas)
    out = "".join(f.text for f in text_frames)
    assert "system_profiler" not in out
    assert out == "Перевіряю. Готово."


@pytest.mark.asyncio
async def test_natural_response_passes_through_unchanged() -> None:
    proc = _make_processor()
    payload = "Звичайно, зачитуй — я уважно слухаю."
    await _send(proc, LLMFullResponseStartFrame())
    await _send(proc, LLMTextFrame(text=payload))
    await _send(proc, LLMFullResponseEndFrame())

    frames = _pushed_frames(proc)
    text_frames = [f for f in frames if isinstance(f, LLMTextFrame)]
    assert text_frames[0].text == payload


@pytest.mark.asyncio
async def test_bash_colon_command_stripped_inline() -> None:
    """`bash: system_profiler ...` mid-sentence is dropped; the
    surrounding sentence survives."""
    proc = _make_processor()
    await _send(proc, LLMFullResponseStartFrame())
    await _send(
        proc, LLMTextFrame(text="Перевіряю. bash: system_profiler SPAudioDataType. Готово.")
    )
    await _send(proc, LLMFullResponseEndFrame())

    frames = _pushed_frames(proc)
    text_frames = [f for f in frames if isinstance(f, LLMTextFrame)]
    out = text_frames[0].text
    assert "system_profiler" not in out
    assert "Перевіряю" in out
    assert "Готово" in out


@pytest.mark.asyncio
async def test_tts_speak_frame_scrubbed() -> None:
    """Standalone TTSSpeakFrames (e.g. startup greeting) are also
    scrubbed — protects the explicit-speech path."""
    proc = _make_processor()
    frame = TTSSpeakFrame(text="list_tools")
    await _send(proc, frame)

    frames = _pushed_frames(proc)
    [out] = frames
    assert isinstance(out, TTSSpeakFrame)
    assert out.text == ""


@pytest.mark.asyncio
async def test_text_frame_outside_response_passes_through() -> None:
    """TextFrames arriving without an LLMFullResponseStartFrame should
    not be mutated — they're not part of an LLM response cycle."""
    proc = _make_processor()
    frame = TextFrame(text="list_tools")
    await _send(proc, frame)

    frames = _pushed_frames(proc)
    [out] = frames
    # Outside a response: not collecting, so frame.text is left alone.
    assert out.text == "list_tools"


@pytest.mark.asyncio
async def test_response_cycle_resets_between_responses() -> None:
    """After one response is suppressed, the next response must start
    fresh — otherwise a single bad reply silences every reply that
    follows."""
    proc = _make_processor()

    # First response: tool-name only, suppressed.
    await _send(proc, LLMFullResponseStartFrame())
    await _send(proc, LLMTextFrame(text="list_tools"))
    await _send(proc, LLMFullResponseEndFrame())

    # Second response: normal text, must pass through.
    await _send(proc, LLMFullResponseStartFrame())
    await _send(proc, LLMTextFrame(text="Готово."))
    await _send(proc, LLMFullResponseEndFrame())

    frames = _pushed_frames(proc)
    text_frames = [f for f in frames if isinstance(f, LLMTextFrame)]
    assert text_frames[0].text == ""
    assert text_frames[1].text == "Готово."


# ---------------------------------------------------------------------------
# Output-driven voice selection
#
# The voice must follow the assembled REPLY text, not the user's input. When
# the agent answers Ukrainian to an English question, an input-driven voice
# would be English on Cyrillic — NoAudioReceived, i.e. silence.


def _voice_processor():
    picked: list[str] = []
    proc = create_tts_scrub_processor(
        voice_setter=picked.append,
        language_fallback=lambda: "uk",
    )
    proc.push_frame = AsyncMock()  # type: ignore[method-assign]
    return proc, picked


@pytest.mark.asyncio
async def test_voice_follows_cyrillic_reply() -> None:
    proc, picked = _voice_processor()

    await _send(proc, LLMFullResponseStartFrame())
    await _send(proc, LLMTextFrame(text="Привіт."))
    await _send(proc, LLMFullResponseEndFrame())

    assert picked == ["uk"]


@pytest.mark.asyncio
async def test_voice_follows_latin_reply() -> None:
    proc, picked = _voice_processor()

    await _send(proc, LLMFullResponseStartFrame())
    await _send(proc, LLMTextFrame(text="Blue."))
    await _send(proc, LLMFullResponseEndFrame())

    assert picked == ["en"]


@pytest.mark.asyncio
async def test_voice_set_from_assembled_chunks() -> None:
    """Streaming chunks may each be short; the choice is made on the join."""
    proc, picked = _voice_processor()

    await _send(proc, LLMFullResponseStartFrame())
    await _send(proc, LLMTextFrame(text="Син"))
    await _send(proc, LLMTextFrame(text="ього."))
    await _send(proc, LLMFullResponseEndFrame())

    assert picked == ["uk"]


@pytest.mark.asyncio
async def test_voice_set_before_text_frames_reach_tts() -> None:
    """The voice must be chosen before any reply frame is pushed downstream,
    or TTS synthesises the first chunk with the old voice."""
    order: list[str] = []
    proc = create_tts_scrub_processor(
        voice_setter=lambda _lang: order.append("voice"),
    )

    async def record_push(frame, direction=None):
        from pipecat.frames.frames import LLMTextFrame as _T

        if isinstance(frame, _T):
            order.append("frame")

    proc.push_frame = record_push  # type: ignore[assignment]

    await _send(proc, LLMFullResponseStartFrame())
    await _send(proc, LLMTextFrame(text="Синього."))
    await _send(proc, LLMFullResponseEndFrame())

    assert order == ["voice", "frame"]


@pytest.mark.asyncio
async def test_voice_fallback_used_when_no_script() -> None:
    """A reply with no script signal ('4.') falls back to the conversation
    language rather than defaulting to English and going silent."""
    proc, picked = _voice_processor()  # fallback lambda returns 'uk'

    await _send(proc, LLMFullResponseStartFrame())
    await _send(proc, LLMTextFrame(text="4."))
    await _send(proc, LLMFullResponseEndFrame())

    assert picked == ["uk"]


@pytest.mark.asyncio
async def test_no_voice_setter_is_harmless() -> None:
    """The default factory (no setter) must still scrub and pass text."""
    proc = _make_processor()

    await _send(proc, LLMFullResponseStartFrame())
    await _send(proc, LLMTextFrame(text="Готово."))
    await _send(proc, LLMFullResponseEndFrame())

    frames = _pushed_frames(proc)
    text_frames = [f for f in frames if isinstance(f, LLMTextFrame)]
    assert text_frames[0].text == "Готово."
