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
        # Dispatch contract: first line matches "Use the <SDK tool> tool: <args>".
        # Phase B-tools normalizes the intent's lowercase tool name to the
        # SDK's CamelCase identifier (edit → Edit).
        first_line = description.split("\n", 1)[0]
        assert first_line.startswith("Use the Edit tool: "), (
            f"dispatch contract violated — got {description!r}"
        )
        args = first_line[len("Use the Edit tool: "):]
        return {"summary": f"ran: {args}"}


def _make_frame(text: str):
    try:
        return TranscriptionFrame(text=text, user_id="u", timestamp="t")
    except TypeError:
        return TranscriptionFrame(user_id="u", timestamp="t", text=text)


async def test_end_to_end_intent_submission_and_execution():
    """PRD US-P2.1-08: stream reply + intent, TTS gets only reply text,
    worker executes intent via FakeClaudeCLI with exact description shape.

    Note: Uses 'edit' tool (complex) instead of 'bash' (simple) to test the Claude
    path. Simple tools (bash, read, write, web_fetch, web_search) route through
    execute_direct() and bypass the CLI entirely.
    """
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

        async def on_result(intent: Intent, summary: str, result: dict) -> None:
            results.append((intent, summary))

        async def on_error(intent: Intent, exc: BaseException) -> None:
            errors.append((intent, exc))

        worker = ActionWorker(queue, fake_claude, on_result, on_error, timeout=5.0)
        worker_task = asyncio.create_task(worker.run())

        stub = StubOpenRouter(
            chunks=[
                "Додам зараз. ",
                '<intent>{"tool":"edit",',
                '"args":"modify file"}</intent>',
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
    assert fake_claude.last_description is not None
    assert fake_claude.last_description.split("\n", 1)[0] == "Use the Edit tool: modify file"
    # (e) Worker called on_result with id=1 and expected summary
    assert len(results) == 1
    assert results[0][0].id == 1
    assert results[0][1] == "ran: modify file"


class _CaptureProcessor:
    """Minimal stand-in for GeneratorProcessor — records pushed frames."""

    def __init__(self) -> None:
        self.pushed: list[Any] = []

    async def push_frame(self, frame: Any) -> None:
        self.pushed.append(frame)

    def tts_texts(self) -> list[str]:
        return [f.text for f in self.pushed if isinstance(f, TTSSpeakFrame)]


async def _run_on_result(summary: str, intent_id: int = 7) -> list[str]:
    from src.actions import Intent
    from src.generator import _scrub_tts_text
    from src.main import _spoken_action_summary

    processor = _CaptureProcessor()
    intent = Intent(id=intent_id, tool="bash", args="x", raw={"tool": "bash"})
    spoken = _spoken_action_summary(summary, _scrub_tts_text)
    await processor.push_frame(TTSSpeakFrame(spoken))
    _ = intent  # intent is part of the callback contract, kept for shape parity
    return processor.tts_texts()


async def _run_on_error(exc: BaseException, intent_id: int = 9) -> list[str]:
    from src.actions import Intent
    from src.main import _action_error_hint

    processor = _CaptureProcessor()
    intent = Intent(id=intent_id, tool="bash", args="x", raw={"tool": "bash"})
    await processor.push_frame(TTSSpeakFrame(_action_error_hint(exc)))
    _ = intent
    return processor.tts_texts()


async def test_intent_flow_emits_result_speech():
    """US-A-04: on_result pushes a TTSSpeakFrame containing the summary prefix."""
    tts = await _run_on_result("ran: echo hi")
    assert tts and "ran: echo hi" in tts[0]


async def test_intent_flow_emits_empty_summary_fallback():
    """US-A-04: empty action summary → fallback phrase spoken so user hears something."""
    from src.main import _ACTION_SUCCESS_FALLBACK

    tts = await _run_on_result("")
    assert tts == [_ACTION_SUCCESS_FALLBACK]


async def test_intent_flow_emits_error_speech_on_timeout():
    """US-A-04: on_error with TimeoutError pushes the Ukrainian timeout hint."""
    from src.main import _ACTION_TIMEOUT_HINT

    tts = await _run_on_error(asyncio.TimeoutError())
    assert tts == [_ACTION_TIMEOUT_HINT]


async def test_intent_flow_emits_error_speech_on_runtime_error():
    """US-A-04: on_error with RuntimeError → hint mentions the class name."""
    tts = await _run_on_error(RuntimeError("nope"))
    assert len(tts) == 1 and "RuntimeError" in tts[0]


async def test_intent_flow_persists_full_turn_to_store():
    """US-B0-03: a complete turn (transcript → reply → intent → action) persists the
    expected rows so the watch dashboard has data in all three columns.

    Expected rows after one "запусти echo hi" turn:
      - 1 transcripts row
      - >=3 decisions rows: speak (reply), act (intent), speak (action result)
      - 1 actions row with status='ok' and result_summary matching the TTS text
    """
    from src.actions import ActionWorker, IntentQueue
    from src.config import Mode, Settings
    from src.context import ContextBuilder
    from src.generator import _scrub_tts_text, create_generator_processor
    from src.main import _persist_action_outcome, _resolve_spoken, _spoken_action_summary
    from src.storage import TranscriptStore

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

        async def on_result(intent: Intent, summary: str, result: dict) -> None:
            results.append((intent, summary))
            # US-VFA-05: use _resolve_spoken so the spoken phrase comes from
            # the result dict's `spoken` key (templated sentence) rather than
            # the legacy _spoken_action_summary truncation path.
            fallback = _spoken_action_summary(summary, _scrub_tts_text)
            spoken = _resolve_spoken(result, intent, fallback_summary=fallback)
            is_success = result.get("success", True) is not False
            status = "ok" if is_success else "error"
            await _persist_action_outcome(store, intent, status, spoken)

        async def on_error(intent: Intent, exc: BaseException) -> None:
            pass  # not exercised here

        worker = ActionWorker(queue, fake_claude, on_result, on_error, timeout=5.0)
        worker_task = asyncio.create_task(worker.run())

        stub = StubOpenRouter(
            chunks=[
                "Додам зараз. ",
                '<intent>{"tool":"edit","args":"modify file"}</intent>',
            ]
        )
        gen = create_generator_processor(
            stub,
            ctx,
            "tpl {transcript}",
            "persona",
            store=store,
            settings=settings,
            intent_queue=queue,
        )
        async def _discard(frame: Any, direction: Any = None) -> None:
            return None

        gen.push_frame = _discard  # type: ignore[method-assign]

        await gen._handle_transcription(_make_frame("запусти echo hi"), None)

        for _ in range(40):
            if results:
                break
            await asyncio.sleep(0.05)

        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

        # Now query the DB directly to verify the three-column payload.
        cur = await store.db.execute("SELECT COUNT(*) FROM transcripts")
        tr_count = (await cur.fetchone())[0]
        assert tr_count == 1, f"expected 1 transcript, got {tr_count}"

        cur = await store.db.execute(
            "SELECT type, reply, intent FROM decisions ORDER BY id"
        )
        decisions = await cur.fetchall()
        types = [d[0] for d in decisions]
        assert "speak" in types, f"expected at least one speak decision: {decisions}"
        assert "act" in types, f"expected at least one act decision: {decisions}"
        # At least: speak(reply), act(intent), speak(result) => 3 rows
        assert len(decisions) >= 3, f"expected >=3 decisions, got {decisions}"

        cur = await store.db.execute(
            "SELECT decision_id, status, result_summary FROM actions ORDER BY id"
        )
        actions = await cur.fetchall()
        assert len(actions) == 1, f"expected 1 action row, got {actions}"
        decision_id, status, result_summary = actions[0]
        assert status == "ok"
        # result_summary is the spoken text (≤120 chars, collapsed whitespace)
        assert "ran: modify file" in result_summary

        # The action row points at the act decision
        cur = await store.db.execute(
            "SELECT type FROM decisions WHERE id = ?", (decision_id,)
        )
        linked_type = (await cur.fetchone())[0]
        assert linked_type == "act"

        await store.close()


async def test_persist_action_outcome_tolerates_missing_decision_id():
    """US-B0-03: Intent without decision_id still logs speech, skips action row."""
    from src.actions import Intent
    from src.main import _persist_action_outcome
    from src.storage import TranscriptStore

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "heare.db"
        store = TranscriptStore(db)
        await store.init()
        intent = Intent(
            id=1, tool="bash", args="x", raw={}, decision_id=None, transcript_id=None
        )
        await _persist_action_outcome(store, intent, "ok", "Готово.")
        cur = await store.db.execute("SELECT COUNT(*) FROM actions")
        assert (await cur.fetchone())[0] == 0  # no decision_id → skipped
        cur = await store.db.execute(
            "SELECT type, reply FROM decisions"
        )
        rows = await cur.fetchall()
        assert rows == [("speak", "Готово.")]
        await store.close()


async def test_persist_action_outcome_none_store_is_noop():
    """US-B0-03: store=None short-circuits — legacy code path."""
    from src.actions import Intent
    from src.main import _persist_action_outcome

    intent = Intent(id=1, tool="bash", args="x", raw={}, decision_id=5, transcript_id=9)
    await _persist_action_outcome(None, intent, "ok", "Готово.")  # does not raise
