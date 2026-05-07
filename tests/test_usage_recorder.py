"""Tests for UsageRecorder — Pipecat stage that turns LLMUsageMetricsData
events into ``usage_events`` rows.

The recorder is observe-only: it forwards every frame downstream and
schedules a fire-and-forget DB write. The tests cover:
  * MetricsFrame containing LLMUsageMetricsData → record_usage_event called
  * known model → cost computed via :mod:`src.agent.llm.pricing` and persisted
  * unknown model → cost recorded as None (caller renders '?')
  * non-MetricsFrame → forwarded untouched, no DB call
  * provider_getter raising → does not crash; provider is empty string
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest


pytest.importorskip("pipecat.frames.frames")
from pipecat.frames.frames import (  # noqa: E402
    LLMTextFrame,
    MetricsFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import (  # noqa: E402
    LLMTokenUsage,
    LLMUsageMetricsData,
    TTSUsageMetricsData,
)

from src.pipeline.stages.usage_recorder import create_usage_recorder  # noqa: E402


class _FakeStore:
    """Minimal stand-in for TranscriptStore.

    Captures kwargs of every ``record_usage_event`` call so tests can
    assert on shape. The recorder schedules these via
    ``asyncio.create_task`` so tests must yield control before
    asserting.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def record_usage_event(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def _make_metrics_frame(
    *, model: str, prompt_tokens: int, completion_tokens: int
) -> MetricsFrame:
    usage = LLMTokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    entry = LLMUsageMetricsData(processor="llm", model=model, value=usage)
    return MetricsFrame(data=[entry])


def _make_recorder(store: _FakeStore, provider: str | None = "openrouter"):
    proc = create_usage_recorder(
        store=store,
        provider_getter=(lambda: provider),
    )
    proc.push_frame = AsyncMock()  # type: ignore[method-assign]
    return proc


async def _drain_tasks() -> None:
    """Yield until all scheduled DB writes have run."""
    # asyncio.sleep(0) cycles the loop once; recorder uses a single
    # create_task so one or two cycles is enough.
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_metrics_frame_with_known_model_records_cost() -> None:
    store = _FakeStore()
    proc = _make_recorder(store)

    frame = _make_metrics_frame(
        model="google/gemini-3.1-flash-lite",
        prompt_tokens=10_000,
        completion_tokens=5_000,
    )
    await proc.process_frame(frame, None)
    await _drain_tasks()

    assert len(store.calls) == 1
    call = store.calls[0]
    assert call["kind"] == "llm"
    assert call["provider"] == "openrouter"
    assert call["model"] == "google/gemini-3.1-flash-lite"
    assert call["input_tokens"] == 10_000
    assert call["output_tokens"] == 5_000
    # 10k input * 0.075/1M + 5k output * 0.30/1M
    assert abs(call["cost_usd"] - 0.00225) < 1e-9


@pytest.mark.asyncio
async def test_metrics_frame_with_unknown_model_records_none_cost() -> None:
    """The recorder still persists the call so token counts stay
    accurate; cost is None so the dashboard shows '?' instead of $0."""
    store = _FakeStore()
    proc = _make_recorder(store)

    frame = _make_metrics_frame(
        model="some/unknown-model-9000",
        prompt_tokens=1_000,
        completion_tokens=500,
    )
    await proc.process_frame(frame, None)
    await _drain_tasks()

    assert len(store.calls) == 1
    call = store.calls[0]
    assert call["model"] == "some/unknown-model-9000"
    assert call["input_tokens"] == 1_000
    assert call["cost_usd"] is None


@pytest.mark.asyncio
async def test_metrics_frame_forwarded_downstream() -> None:
    """The recorder must be observe-only — every frame is pushed onward
    so removing the stage from the pipeline never silences usage events
    for any consumer."""
    store = _FakeStore()
    proc = _make_recorder(store)

    frame = _make_metrics_frame(
        model="google/gemini-3.1-flash-lite",
        prompt_tokens=100,
        completion_tokens=50,
    )
    await proc.process_frame(frame, None)
    await _drain_tasks()

    pushed = [c.args[0] for c in proc.push_frame.await_args_list]
    assert frame in pushed


@pytest.mark.asyncio
async def test_non_metrics_frame_passes_through_without_record() -> None:
    """An ordinary LLMTextFrame must be forwarded untouched and never
    trigger a DB write — usage tracking only fires on MetricsFrame."""
    store = _FakeStore()
    proc = _make_recorder(store)

    frame = LLMTextFrame(text="hello")
    await proc.process_frame(frame, None)
    await _drain_tasks()

    assert store.calls == []
    pushed = [c.args[0] for c in proc.push_frame.await_args_list]
    assert pushed == [frame]


@pytest.mark.asyncio
async def test_provider_getter_exception_does_not_crash() -> None:
    """A failing ``provider_getter`` must be swallowed (logged) so the
    audio loop never fails because of an instrumentation glitch."""
    store = _FakeStore()

    def _bad() -> str:
        raise RuntimeError("boom")

    proc = create_usage_recorder(store=store, provider_getter=_bad)
    proc.push_frame = AsyncMock()  # type: ignore[method-assign]

    frame = _make_metrics_frame(
        model="google/gemini-3.1-flash-lite",
        prompt_tokens=100,
        completion_tokens=50,
    )
    await proc.process_frame(frame, None)
    await _drain_tasks()

    assert len(store.calls) == 1
    # Provider falls back to None when the getter raises (the recorder
    # collapses empty-string provider to None before the DB write).
    assert store.calls[0]["provider"] is None


@pytest.mark.asyncio
async def test_metrics_frame_with_no_llm_data_does_not_record() -> None:
    """A MetricsFrame may carry non-LLM metrics — those should pass
    through without producing a usage row."""
    store = _FakeStore()
    proc = _make_recorder(store)

    # Empty data list is the simplest 'no LLM usage' case.
    frame = MetricsFrame(data=[])
    await proc.process_frame(frame, None)
    await _drain_tasks()

    assert store.calls == []
    pushed = [c.args[0] for c in proc.push_frame.await_args_list]
    assert pushed == [frame]


# ---------------------------------------------------------------------------
# TTS — TTSUsageMetricsData
# ---------------------------------------------------------------------------


def _make_tts_metrics_frame(*, char_count: int, processor: str = "tts") -> MetricsFrame:
    entry = TTSUsageMetricsData(processor=processor, model=None, value=char_count)
    return MetricsFrame(data=[entry])


def _make_recorder_with_providers(
    store: _FakeStore,
    *,
    stt_provider: str | None = "groq-whisper-large-v3",
    tts_provider: str | None = "edge_tts",
):
    proc = create_usage_recorder(
        store=store,
        provider_getter=lambda: "openrouter",
        stt_provider=stt_provider,
        tts_provider=tts_provider,
    )
    proc.push_frame = AsyncMock()  # type: ignore[method-assign]
    return proc


@pytest.mark.asyncio
async def test_tts_metrics_frame_records_call_even_when_free() -> None:
    """``edge_tts`` is $0/char — but the row must still land so the user
    can see call volume in statistics."""
    store = _FakeStore()
    proc = _make_recorder_with_providers(store)

    await proc.process_frame(_make_tts_metrics_frame(char_count=120), None)
    await _drain_tasks()

    assert len(store.calls) == 1
    call = store.calls[0]
    assert call["kind"] == "tts"
    assert call["provider"] == "edge_tts"
    assert call["char_count"] == 120
    # edge_tts pricing returns 0.0, not None.
    assert call["cost_usd"] == 0.0


@pytest.mark.asyncio
async def test_tts_metrics_frame_unknown_provider_records_none_cost() -> None:
    """When the configured TTS provider isn't in the price table, we
    still record the call so totals stay accurate."""
    store = _FakeStore()
    proc = _make_recorder_with_providers(store, tts_provider="some-unknown-tts")

    await proc.process_frame(_make_tts_metrics_frame(char_count=50), None)
    await _drain_tasks()

    assert len(store.calls) == 1
    assert store.calls[0]["kind"] == "tts"
    assert store.calls[0]["char_count"] == 50
    assert store.calls[0]["cost_usd"] is None


@pytest.mark.asyncio
async def test_tts_metrics_frame_zero_chars_does_not_record() -> None:
    """A zero-character TTS metric is noise (e.g. cancelled stream); skip
    it so we don't pollute the ledger with empty rows."""
    store = _FakeStore()
    proc = _make_recorder_with_providers(store)

    await proc.process_frame(_make_tts_metrics_frame(char_count=0), None)
    await _drain_tasks()

    assert store.calls == []


# ---------------------------------------------------------------------------
# STT — TranscriptionFrame + VAD bracket
# ---------------------------------------------------------------------------


def _make_transcription(text: str, *, finalized: bool = True) -> TranscriptionFrame:
    return TranscriptionFrame(
        text=text, user_id="user", timestamp="2026-05-03T20:00:00Z",
        finalized=finalized,
    )


@pytest.mark.asyncio
async def test_finalized_transcription_records_stt_call() -> None:
    """Each finalized TranscriptionFrame is one completed STT call."""
    store = _FakeStore()
    proc = _make_recorder_with_providers(store)

    await proc.process_frame(_make_transcription("hello"), None)
    await _drain_tasks()

    assert len(store.calls) == 1
    call = store.calls[0]
    assert call["kind"] == "stt"
    assert call["provider"] == "groq-whisper-large-v3"


@pytest.mark.asyncio
async def test_interim_transcription_does_not_record() -> None:
    """Streaming STT services emit interim frames with finalized=False;
    only finals count toward the call total."""
    store = _FakeStore()
    proc = _make_recorder_with_providers(store)

    await proc.process_frame(_make_transcription("partial", finalized=False), None)
    await _drain_tasks()

    assert store.calls == []


@pytest.mark.asyncio
async def test_empty_transcription_does_not_record() -> None:
    """STT services occasionally emit a finalized frame with empty text
    (no speech detected). Don't count those as calls."""
    store = _FakeStore()
    proc = _make_recorder_with_providers(store)

    await proc.process_frame(_make_transcription("   "), None)
    await _drain_tasks()

    assert store.calls == []


@pytest.mark.asyncio
async def test_vad_bracket_attaches_audio_seconds_to_next_transcription() -> None:
    """``UserStartedSpeakingFrame`` → ``UserStoppedSpeakingFrame`` defines
    the audio duration; that delta must land on the next finalized
    TranscriptionFrame so the cost calculator has real seconds."""
    store = _FakeStore()
    proc = _make_recorder_with_providers(store)

    # Force a known monotonic delta by patching the clock. The
    # recorder calls ``time.monotonic`` exactly twice — once on
    # UserStartedSpeaking, once on UserStoppedSpeaking. Returning the
    # last value indefinitely guards against any incidental call
    # pipecat internals make.
    import src.pipeline.stages.usage_recorder as ur

    values = [100.0, 102.5]
    idx = {"i": 0}

    def monkey_clock() -> float:
        i = min(idx["i"], len(values) - 1)
        idx["i"] += 1
        return values[i]

    real_monotonic = ur.time.monotonic
    ur.time.monotonic = monkey_clock  # type: ignore[assignment]
    try:
        await proc.process_frame(UserStartedSpeakingFrame(), None)
        await proc.process_frame(UserStoppedSpeakingFrame(), None)
        await proc.process_frame(_make_transcription("hi there"), None)
        await _drain_tasks()
    finally:
        ur.time.monotonic = real_monotonic  # type: ignore[assignment]

    assert len(store.calls) == 1
    call = store.calls[0]
    assert call["kind"] == "stt"
    assert abs(call["audio_seconds"] - 2.5) < 1e-6
    # 2.5s of groq whisper = (2.5/3600) * 0.04 ≈ 0.0000278
    assert call["cost_usd"] is not None
    assert abs(call["cost_usd"] - (2.5 / 3600 * 0.04)) < 1e-9


@pytest.mark.asyncio
async def test_audio_seconds_consumed_per_transcription() -> None:
    """The recorder must clear the speech duration after using it so two
    transcriptions in a row don't both inherit the same bracket."""
    store = _FakeStore()
    proc = _make_recorder_with_providers(store)

    import src.pipeline.stages.usage_recorder as ur

    values = [0.0, 1.0]
    idx = {"i": 0}

    def monkey_clock() -> float:
        i = min(idx["i"], len(values) - 1)
        idx["i"] += 1
        return values[i]

    real_monotonic = ur.time.monotonic
    ur.time.monotonic = monkey_clock  # type: ignore[assignment]
    try:
        await proc.process_frame(UserStartedSpeakingFrame(), None)
        await proc.process_frame(UserStoppedSpeakingFrame(), None)
        await proc.process_frame(_make_transcription("first"), None)
        await proc.process_frame(_make_transcription("second"), None)
        await _drain_tasks()
    finally:
        ur.time.monotonic = real_monotonic  # type: ignore[assignment]

    assert len(store.calls) == 2
    # First call gets the 1s bracket; second falls back to 0s because
    # the bracket is consumed.
    assert abs(store.calls[0]["audio_seconds"] - 1.0) < 1e-6
    assert store.calls[1]["audio_seconds"] == 0.0
