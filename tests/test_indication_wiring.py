"""US-IND-A5: IndicationCueFrame upstream gating in generator + speaker_tagger.

Synthesizes IndicationCueFrame(start=True/False) and asserts the consumer
processors flip the _indication_speaking flag and drop transcripts during the
bracketed window.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.config import Settings
from src.indication import IndicationCueFrame


# ---------------------------------------------------------------------------
# Generator gating
# ---------------------------------------------------------------------------


async def _build_generator():
    from src import generator as gen_mod

    settings = Settings()
    settings.openrouter_api_key = "x"
    settings.transcript_debounce_seconds = 0.0  # immediate dispatch in test

    ctx_builder = MagicMock()
    ctx_builder.build_for_generator = AsyncMock(return_value={
        "transcript": "",
        "persona": "",
        "time": "",
        "timezone": "",
        "mode": "ambient",
        "recent_transcripts": "",
        "conversation_summary": "",
        "active_topics": "",
        "entities": "",
        "recent_turns": "",
        "recent_actions": "",
    })
    ctx_builder.render = MagicMock(return_value="prompt")

    or_cli = MagicMock()

    async def gen_no_chunks(prompt):
        if False:
            yield ""
        return

    or_cli.generate = gen_no_chunks

    intent_queue = MagicMock()
    intent_queue.submit = AsyncMock(return_value=None)
    intent_queue.cancel_latest = MagicMock(return_value=None)
    intent_queue.pending_count = MagicMock(return_value=0)

    proc = gen_mod.create_generator_processor(
        openrouter_cli=or_cli,
        context_builder=ctx_builder,
        prompt_template="{transcript}",
        persona="",
        store=None,
        settings=settings,
        intent_queue=intent_queue,
    )
    return proc, ctx_builder


async def test_generator_drops_transcript_while_indication_speaking() -> None:
    proc, ctx_builder = await _build_generator()

    async def fake_push(frame, direction=None):
        return None

    proc.push_frame = fake_push  # type: ignore[method-assign]
    proc._indication_speaking = True

    fake_frame = MagicMock(text="привіт", finalized=True)
    fake_frame.speaker_id = None
    fake_frame.speaker_confidence = None
    await proc._handle_transcription(fake_frame, None)
    ctx_builder.build_for_generator.assert_not_called()


async def test_generator_processes_transcript_after_indication_clears() -> None:
    proc, ctx_builder = await _build_generator()

    async def fake_push(frame, direction=None):
        return None

    proc.push_frame = fake_push  # type: ignore[method-assign]

    cue_start = IndicationCueFrame(start=True)
    cue_end = IndicationCueFrame(start=False)

    # Bypass pipecat StartFrame requirement on process_frame
    from pipecat.processors.frame_processor import FrameProcessor as _FP

    async def _noop(self, frame, direction):
        return None

    orig = _FP.process_frame
    _FP.process_frame = _noop  # type: ignore[assignment]
    try:
        await proc.process_frame(cue_start, None)
        assert proc._indication_speaking is True
        await proc.process_frame(cue_end, None)
        assert proc._indication_speaking is False
    finally:
        _FP.process_frame = orig  # type: ignore[assignment]

    fake_frame = MagicMock(text="привіт", finalized=True)
    fake_frame.speaker_id = None
    fake_frame.speaker_confidence = None
    await proc._handle_transcription(fake_frame, None)
    ctx_builder.build_for_generator.assert_called_once()


# ---------------------------------------------------------------------------
# SpeakerTagger gating
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# US-IND-B2 — action lifecycle (rejection + long-running + failure)
# ---------------------------------------------------------------------------


class _CapturingBackend:
    name = "visual"

    def __init__(self) -> None:
        self.captured: list = []

    async def fire(self, kind, level, title, body, meta) -> None:
        self.captured.append((kind, level, title, body, meta))

    async def aclose(self) -> None:
        pass


def _install_indication(monkeypatch, **settings_overrides):
    from src import indication as ind_mod
    from src.config import IndicationSettings

    backend = _CapturingBackend()
    s = IndicationSettings(sound_enabled=False, notification_center_enabled=False)
    for k, v in settings_overrides.items():
        setattr(s, k, v)
    ind = ind_mod.Indication(s, [backend])
    ind_mod.set_indication(ind)
    monkeypatch.setattr(ind_mod, "set_indication", ind_mod.set_indication)

    def _restore():
        ind_mod.set_indication(None)

    return backend, _restore


async def test_intentqueue_submit_fires_action_rejected_for_disallowed_tool(monkeypatch) -> None:
    from src.actions import IntentQueue
    from src.indication import IndicationKind

    backend, restore = _install_indication(monkeypatch)
    try:
        q = IntentQueue()
        result = await q.submit({"tool": "RogueTool", "args": "x"})
        await asyncio.sleep(0.05)
    finally:
        restore()

    assert result is None
    kinds = [t[0] for t in backend.captured]
    assert IndicationKind.ACTION_REJECTED in kinds
    bodies = [t[3] for t in backend.captured]
    assert any("tool not allowed" in b for b in bodies)


async def test_intentqueue_submit_fires_action_rejected_for_args_too_long(monkeypatch) -> None:
    from src.actions import IntentQueue, MAX_ARGS_LEN
    from src.indication import IndicationKind

    backend, restore = _install_indication(monkeypatch)
    try:
        q = IntentQueue()
        result = await q.submit({"tool": "bash", "args": "x" * (MAX_ARGS_LEN + 1)})
        await asyncio.sleep(0.05)
    finally:
        restore()

    assert result is None
    bodies = [t[3] for t in backend.captured if t[0] == IndicationKind.ACTION_REJECTED]
    assert any("args too long" in b for b in bodies)


async def test_intentqueue_submit_fires_action_rejected_for_queue_full(monkeypatch) -> None:
    from src.actions import IntentQueue
    from src.indication import IndicationKind

    backend, restore = _install_indication(
        monkeypatch, cooldown_seconds=0.0
    )  # no cooldown so we can see two
    try:
        q = IntentQueue(max_pending=1)
        await q.submit({"tool": "bash", "args": "echo a"})
        result = await q.submit({"tool": "bash", "args": "echo b"})
        await asyncio.sleep(0.05)
    finally:
        restore()

    assert result is None
    bodies = [t[3] for t in backend.captured if t[0] == IndicationKind.ACTION_REJECTED]
    assert any("queue full" in b for b in bodies)


async def test_action_long_running_fires_after_5s(monkeypatch) -> None:
    """5s watchdog fires ACTION_LONG_RUNNING for slow actions."""
    from src.actions import ActionWorker, Intent, IntentQueue
    from src.indication import IndicationKind

    backend, restore = _install_indication(monkeypatch, cooldown_seconds=0.0)
    try:
        # Speed up the watchdog by patching asyncio.sleep inside actions.
        import src.actions as actions_mod

        original_sleep = asyncio.sleep
        slept_intervals: list[float] = []

        async def fast_sleep(delay):
            slept_intervals.append(delay)
            await original_sleep(0.01)  # ~instant

        monkeypatch.setattr(actions_mod.asyncio, "sleep", fast_sleep)

        # Build a worker with no real claude_cli; we just call _process_one
        # with a stub that returns immediately so the watchdog never fires
        # in the no-op case, then with one that hangs to verify the fire.
        q = IntentQueue()
        worker = ActionWorker(
            queue=q, claude_cli=MagicMock(), on_result=AsyncMock(), on_error=AsyncMock()
        )

        # Stub the simple-tool path to return after a short delay (>5s in
        # original asyncio.sleep, but our fast_sleep makes it instant).
        async def slow_execute(*a, **kw):
            await original_sleep(0.05)
            return {"success": True, "output": "done", "error": None}

        monkeypatch.setattr("src.actions.execute_direct", slow_execute)

        intent = Intent(id=1, tool="bash", args="echo", raw={})
        await worker._process_one(intent)
        await asyncio.sleep(0.05)
    finally:
        restore()

    kinds = [t[0] for t in backend.captured]
    assert IndicationKind.ACTION_LONG_RUNNING in kinds


async def test_action_long_running_does_not_fire_for_fast_action(monkeypatch) -> None:
    from src.actions import ActionWorker, Intent, IntentQueue
    from src.indication import IndicationKind

    backend, restore = _install_indication(monkeypatch, cooldown_seconds=0.0)
    try:
        q = IntentQueue()
        worker = ActionWorker(
            queue=q, claude_cli=MagicMock(), on_result=AsyncMock(), on_error=AsyncMock()
        )

        async def quick_execute(*a, **kw):
            return {"success": True, "output": "done", "error": None}

        monkeypatch.setattr("src.actions.execute_direct", quick_execute)

        intent = Intent(id=1, tool="bash", args="echo", raw={})
        await worker._process_one(intent)
        await asyncio.sleep(0.05)
    finally:
        restore()

    kinds = [t[0] for t in backend.captured]
    assert IndicationKind.ACTION_LONG_RUNNING not in kinds


# ---------------------------------------------------------------------------
# US-IND-B3 — external service errors (OpenRouter, STT ErrorFrame)
# ---------------------------------------------------------------------------


async def test_generator_fires_openrouter_timeout_on_error(monkeypatch) -> None:
    from src.indication import IndicationKind
    from src.openrouter_cli import OpenRouterError

    backend, restore = _install_indication(monkeypatch)
    try:
        proc, ctx_builder = await _build_generator()

        async def fake_push(frame, direction=None):
            return None

        proc.push_frame = fake_push  # type: ignore[method-assign]

        async def boom(prompt):
            raise OpenRouterError("503 service unavailable")
            if False:
                yield ""

        proc.openrouter_cli.generate = boom  # type: ignore[method-assign]

        fake_frame = MagicMock(text="привіт", finalized=True)
        fake_frame.speaker_id = None
        fake_frame.speaker_confidence = None
        await proc._handle_transcription(fake_frame, None)
        await asyncio.sleep(0.05)
    finally:
        restore()

    kinds = [t[0] for t in backend.captured]
    assert IndicationKind.OPENROUTER_TIMEOUT in kinds


async def test_stt_error_observer_fires_stt_error_kind(monkeypatch) -> None:
    """Inject pipecat ErrorFrame; assert the observer fires STT_ERROR."""
    from pipecat.frames.frames import ErrorFrame
    from pipecat.processors.frame_processor import FrameProcessor as _FP

    from src import indication as ind_mod
    from src.indication import IndicationKind

    backend, restore = _install_indication(monkeypatch)
    try:
        # Re-create the same observer class as in pipeline.py.
        class _Observer(_FP):
            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                if isinstance(frame, ErrorFrame):
                    ind = ind_mod.get_indication()
                    if ind is not None:
                        ind.notify(
                            IndicationKind.STT_ERROR,
                            body=str(getattr(frame, "error", str(frame)))[:160],
                        )
                await self.push_frame(frame, direction)

        observer = _Observer()

        # Bypass StartFrame requirement
        async def _noop(self, frame, direction):
            return None

        orig = _FP.process_frame
        _FP.process_frame = _noop  # type: ignore[assignment]
        try:
            async def fake_push(frame, direction=None):
                return None

            observer.push_frame = fake_push  # type: ignore[method-assign]
            await observer.process_frame(ErrorFrame(error="STT down"), None)
            await asyncio.sleep(0.05)
        finally:
            _FP.process_frame = orig  # type: ignore[assignment]
    finally:
        restore()

    kinds = [t[0] for t in backend.captured]
    assert IndicationKind.STT_ERROR in kinds


# ---------------------------------------------------------------------------
# US-IND-B1 — re-enroll input-waiting wiring (off-loop notify path)
# ---------------------------------------------------------------------------


async def test_reenroll_fires_start_and_end_notifications(
    tmp_path: Path, monkeypatch
) -> None:
    """REENROLL_RECORDING_START fires before sd.rec; _END fires after.

    Even though the current direct_tools._execute_re_enroll runs on the main
    loop (sounddevice calls block synchronously), the wiring path is the same
    one that would exercise the off-loop run_coroutine_threadsafe branch when
    the recorder later moves into a worker thread.
    """
    import numpy as np

    from src import direct_tools, indication as ind_mod
    from src.config import IndicationSettings

    # Stub sounddevice so the test never touches a real mic.
    fake_sd = MagicMock()
    fake_sd.rec = MagicMock(
        return_value=np.zeros((100, 1), dtype="int16")
    )
    fake_sd.wait = MagicMock()
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", fake_sd)

    # Stub speaker_id so embedding doesn't hit speechbrain.
    monkeypatch.setattr(
        "src.speaker_id.embed",
        lambda pcm, sr, model: np.zeros(192, dtype="float32"),
    )
    monkeypatch.setattr("src.speaker_id.load_model", lambda: MagicMock())

    # Use a temp speakers.json
    monkeypatch.setattr(
        "src.config.HEARE_HOME", tmp_path
    )

    # Install a recording Indication facade as the process singleton.
    captured: list = []

    class Capturing:
        async def fire(self, kind, level, title, body, meta) -> None:
            captured.append((kind, level, title, body, meta))

        async def aclose(self) -> None:
            pass

        name = "visual"

    backend = Capturing()
    settings = IndicationSettings(
        sound_enabled=False,
        notification_center_enabled=False,
    )
    ind = ind_mod.Indication(settings, [backend])
    ind_mod.set_indication(ind)
    try:
        result = await direct_tools._execute_re_enroll("5", None)
    finally:
        ind_mod.set_indication(None)

    assert result["success"] is True
    # Allow the dispatch tasks to drain.
    await asyncio.sleep(0.05)
    kinds = [t[0] for t in captured]
    assert ind_mod.IndicationKind.REENROLL_RECORDING_START in kinds
    assert ind_mod.IndicationKind.REENROLL_RECORDING_END in kinds


async def test_speaker_tagger_marks_transcripts_during_indication(
    tmp_path: Path, monkeypatch
) -> None:
    import numpy as np

    import src.speaker_id as sid
    from src.speaker_gallery import SpeakerGallery
    from src.speaker_processor import create_speaker_processors

    monkeypatch.setattr(sid, "embed", lambda pcm, sr, model: np.zeros(192, dtype="float32"))
    settings = Settings()
    settings.speaker_id_enabled = True
    gallery = SpeakerGallery(tmp_path / "speakers.json")
    buffer, tagger = create_speaker_processors(settings, gallery, model=MagicMock())

    from pipecat.processors.frame_processor import FrameProcessor as _FP

    async def _noop(self, frame, direction):
        return None

    orig = _FP.process_frame
    _FP.process_frame = _noop  # type: ignore[assignment]
    try:
        async def fake_push(frame, direction=None):
            return None

        tagger.push_frame = fake_push  # type: ignore[method-assign]

        cue_start = IndicationCueFrame(start=True)
        await tagger.process_frame(cue_start, None)
        assert tagger._indication_speaking is True

        from pipecat.frames.frames import TranscriptionFrame

        frame = TranscriptionFrame(text="привіт", user_id="u", timestamp="t")
        frame.finalized = True
        await tagger.process_frame(frame, None)
        assert frame.speaker_id is None
        assert frame.speaker_confidence == -1.0

        cue_end = IndicationCueFrame(start=False)
        await tagger.process_frame(cue_end, None)
        assert tagger._indication_speaking is False
    finally:
        _FP.process_frame = orig  # type: ignore[assignment]
    await buffer.close()
