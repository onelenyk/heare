"""End-to-end intent flow test for Phase 2.1 US-P2.1-08.

Exercises: TranscriptionFrame → GeneratorProcessor → OpenRouter stream →
IntentStreamParser → IntentQueue → ActionWorker → FakeClaudeCLI.

The FakeClaudeCLI asserts the dispatch contract (description shape =
"Use the {tool} tool: {args}") so the test fails if the worker passes
the wrong argument shape.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import AsyncIterator, Any

import pytest

pytest.importorskip("pipecat.frames.frames")
from pipecat.frames.frames import TranscriptionFrame, TTSSpeakFrame  # noqa: E402

from src.actions import ActionWorker, Intent, IntentQueue  # noqa: E402
from src.config import Mode, Settings  # noqa: E402
from src.context import ContextBuilder  # noqa: E402
from src.generator import create_generator_processor  # noqa: E402
from src.storage import TranscriptStore  # noqa: E402


class StubOpenRouter:
    """Yields scripted chunks to test streaming + intent extraction."""

    def __init__(self, chunks: list[str]):
        self.chunks = chunks

    async def generate(self, prompt: str, system: str | None = None) -> AsyncIterator[str]:
        for c in self.chunks:
            yield c


class FakeClaudeCLI:
    """Asserts the dispatch contract inside call_action."""

    def __init__(self):
        self.last_description: str | None = None

    async def call_action(self, description: str) -> dict[str, str]:
        self.last_description = description
        # Enforce contract: description must match "Use the {tool} tool: {args}"
        assert description.startswith("Use the bash tool: "), (
            f"dispatch contract violated — got {description!r}"
        )
        args = description[len("Use the bash tool: "):]
        return {"summary": f"ran: {args}"}


def _make_frame(text: str):
    try:
        return TranscriptionFrame(text=text, user_id="u", timestamp="t")
    except TypeError:
        return TranscriptionFrame(user_id="u", timestamp="t", text=text)


async def test_end_to_end_intent_submission_and_execution():
    """PRD US-P2.1-08: stream reply + intent, TTS gets only reply text,
    worker executes intent via FakeClaudeCLI with exact description shape."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "heare.db"
        store = TranscriptStore(db)
        await store.init()
        settings = Settings()
        settings.mode = Mode.AMBIENT
        ctx = ContextBuilder(store, settings)

        queue = IntentQueue()
        fake_claude = FakeClaudeCLI()

        results: list[tuple[Intent, str]] = []
        errors: list[tuple[Intent, BaseException]] = []

        async def on_result(intent: Intent, summary: str) -> None:
            results.append((intent, summary))

        async def on_error(intent: Intent, exc: BaseException) -> None:
            errors.append((intent, exc))

        worker = ActionWorker(queue, fake_claude, on_result, on_error, timeout=5.0)
        worker_task = asyncio.create_task(worker.run())

        stub = StubOpenRouter(
            chunks=[
                "Додам зараз. ",
                '<intent>{"tool":"bash",',
                '"args":"echo hi"}</intent>',
            ]
        )
        gen = create_generator_processor(
            stub,
            ctx,
            "tpl {transcript}",
            "persona",
            intent_queue=queue,
        )

        pushed: list[Any] = []

        async def capture(frame, direction=None):
            pushed.append(frame)

        gen.push_frame = capture  # type: ignore[assignment]

        await gen._handle_transcription(_make_frame("запусти echo hi"), None)

        # Wait for worker to process the intent
        for _ in range(40):
            if results:
                break
            await asyncio.sleep(0.05)

        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        await store.close()

    # (a) TTS received "Додам зараз." (exact prefix match)
    tts_texts = [f.text for f in pushed if isinstance(f, TTSSpeakFrame)]
    assert any("Додам зараз" in t for t in tts_texts)
    # (b) No '<' anywhere in TTS output
    for t in tts_texts:
        assert "<" not in t
    # (d) FakeClaudeCLI received description matching the contract
    assert fake_claude.last_description == "Use the bash tool: echo hi"
    # (e) Worker called on_result with id=1 and expected summary
    assert len(results) == 1
    assert results[0][0].id == 1
    assert results[0][1] == "ran: echo hi"
