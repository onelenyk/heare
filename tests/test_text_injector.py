"""Tests for src/text_injector.py — file-queue IPC for injecting text into
a running daemon as if it came from STT."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


def test_inject_text_writes_atomic_file(tmp_path: Path):
    from src.pipeline.stages.text_injector import inject_text

    folder = tmp_path / "inject"
    path = inject_text(folder, "  hello world  ")

    assert path.exists()
    assert path.parent == folder
    assert path.suffix == ".txt"
    # Whitespace stripped on the way in (avoids double-stripping at consume).
    assert path.read_text() == "hello world"


def test_inject_text_rejects_empty(tmp_path: Path):
    from src.pipeline.stages.text_injector import inject_text

    with pytest.raises(ValueError):
        inject_text(tmp_path, "   ")


def test_drain_once_orders_chronologically(tmp_path: Path):
    from src.pipeline.stages.text_injector import _drain_once, inject_text

    p1 = inject_text(tmp_path, "first")
    p2 = inject_text(tmp_path, "second")
    items = _drain_once(tmp_path)
    assert [t for _, t in items] == ["first", "second"]
    # ensure both paths returned (we'll later delete them)
    assert {p for p, _ in items} == {p1, p2}


def test_drain_once_skips_non_txt(tmp_path: Path):
    from src.pipeline.stages.text_injector import _drain_once, inject_text

    inject_text(tmp_path, "real")
    (tmp_path / "stray.tmp").write_text("ignore me")
    (tmp_path / "stray.json").write_text("{}")

    items = _drain_once(tmp_path)
    assert [t for _, t in items] == ["real"]


@pytest.mark.asyncio
async def test_run_injector_loop_drains_and_deletes(tmp_path: Path):
    from src.pipeline.stages.text_injector import inject_text, run_injector_loop

    inject_text(tmp_path, "alpha")
    inject_text(tmp_path, "beta")

    pushed: list[str] = []
    stop = asyncio.Event()

    async def push(text: str) -> None:
        pushed.append(text)
        if len(pushed) >= 2:
            stop.set()

    await asyncio.wait_for(
        run_injector_loop(tmp_path, push, poll_interval=0.01, stop_event=stop),
        timeout=2.0,
    )
    assert pushed == ["alpha", "beta"]
    # Files cleaned up.
    assert list(tmp_path.glob("*.txt")) == []


@pytest.mark.asyncio
async def test_run_injector_loop_keeps_message_when_push_raises(tmp_path: Path):
    """If push() raises we still delete (best-effort) so a poison message
    can't deadlock the queue. Verify deletion happens after push error."""
    from src.pipeline.stages.text_injector import inject_text, run_injector_loop

    inject_text(tmp_path, "boom")

    seen: list[str] = []
    stop = asyncio.Event()

    async def push(text: str) -> None:
        seen.append(text)
        stop.set()
        raise RuntimeError("simulated downstream failure")

    await asyncio.wait_for(
        run_injector_loop(tmp_path, push, poll_interval=0.01, stop_event=stop),
        timeout=2.0,
    )
    assert seen == ["boom"]
    # File removed despite the push error.
    assert list(tmp_path.glob("*.txt")) == []


@pytest.mark.asyncio
async def test_pusher_appends_message_and_requests_completion(tmp_path: Path):
    """The frame must both carry the text and ask the LLM to run.

    A TranscriptionFrame here would be silently swallowed: the user
    aggregator buffers transcriptions and only flushes them via an
    audio-driven turn-stop strategy, so injected text would never reach the
    LLM (and would contaminate the next spoken turn).
    """
    from pipecat.frames.frames import LLMMessagesAppendFrame

    from src.pipeline.stages.text_injector import make_llm_message_pusher

    captured: list = []

    class FakeProcessor:
        async def push_frame(self, frame, direction=None):
            captured.append(frame)

    push = make_llm_message_pusher(FakeProcessor())
    await push("hello daemon")

    assert len(captured) == 1
    frame = captured[0]
    assert isinstance(frame, LLMMessagesAppendFrame)
    assert frame.messages == [{"role": "user", "content": "hello daemon"}]
    # Without run_llm the message lands in the context but nothing generates.
    assert frame.run_llm is True


@pytest.mark.asyncio
async def test_pusher_role_is_overridable(tmp_path: Path):
    from src.pipeline.stages.text_injector import make_llm_message_pusher

    captured: list = []

    class FakeProcessor:
        async def push_frame(self, frame, direction=None):
            captured.append(frame)

    await make_llm_message_pusher(FakeProcessor(), role="system")("note")
    assert captured[0].messages == [{"role": "system", "content": "note"}]


@pytest.mark.asyncio
async def test_pusher_sets_voice_for_cyrillic_text(tmp_path: Path):
    """Cyrillic read by an English voice yields NoAudioReceived — silence.

    Injected text bypasses the transcription gate, so the pusher has to drive
    the voice swap itself or the reply is generated and never heard.
    """
    from src.pipeline.stages.text_injector import make_llm_message_pusher

    langs: list = []

    class FakeProcessor:
        async def push_frame(self, frame, direction=None):
            pass

    push = make_llm_message_pusher(
        FakeProcessor(), voice_setter=langs.append, language_fallback="uk"
    )
    await push("Скільки буде два плюс два?")
    assert langs == ["uk"]

    await push("How much is two plus two?")
    assert langs == ["uk", "en"]


@pytest.mark.asyncio
async def test_pusher_survives_voice_setter_failure(tmp_path: Path):
    """A voice we cannot set must not cost us the message."""
    from src.pipeline.stages.text_injector import make_llm_message_pusher

    captured: list = []

    class FakeProcessor:
        async def push_frame(self, frame, direction=None):
            captured.append(frame)

    def boom(_lang):
        raise RuntimeError("tts unavailable")

    await make_llm_message_pusher(FakeProcessor(), voice_setter=boom)("hi")
    assert len(captured) == 1
