"""US-IND-A4a: SoundCueProcessor and SoundBackend behavior."""
from __future__ import annotations

import asyncio

import pytest

from src import indication_assets
from src.indication import (
    IndicationCueFrame,
    IndicationKind,
    IndicationLevel,
    build_sound_cue_processor,
)
from src.indication_backends.sound import SoundBackend


def _patch_push_frame(processor):
    """Bypass pipecat's StartFrame requirement by replacing push_frame
    with a recorder. Returns the captured-frames list.
    """
    captured: list = []

    async def fake_push(frame, direction=None):
        captured.append(frame)

    processor.push_frame = fake_push  # type: ignore[method-assign]
    return captured


async def test_processor_emits_three_frame_sequence_per_cue() -> None:
    proc = build_sound_cue_processor(sample_rate=24000)
    captured = _patch_push_frame(proc)
    pcm = indication_assets.attention_cue(24000)

    proc.enqueue_cue(pcm)
    # Wait long enough for cue duration + pad.
    await asyncio.sleep(0.5)

    assert len(captured) == 3, f"expected 3 frames, got {len(captured)}: {captured}"
    assert isinstance(captured[0], IndicationCueFrame) and captured[0].start is True

    # Middle frame must be OutputAudioRawFrame, NOT bare AudioRawFrame.
    from pipecat.frames.frames import AudioRawFrame, OutputAudioRawFrame  # type: ignore

    assert isinstance(captured[1], OutputAudioRawFrame)
    # Ensure subclass distinction is enforced (defense against accidental
    # downgrade to a non-routable bare AudioRawFrame).
    assert type(captured[1]) is OutputAudioRawFrame or isinstance(
        captured[1], AudioRawFrame
    )
    assert captured[1].audio == pcm
    assert captured[1].sample_rate == 24000

    assert isinstance(captured[2], IndicationCueFrame) and captured[2].start is False
    await proc.aclose()


@pytest.mark.parametrize(
    "pcm_factory",
    [
        lambda: indication_assets.info_cue(24000),  # short
        lambda: indication_assets.error_cue(24000),  # longer
    ],
)
async def test_tail_timing_matches_formula(pcm_factory) -> None:
    proc = build_sound_cue_processor(sample_rate=24000, tail_pad_seconds=0.2)
    timestamps: list[float] = []

    loop = asyncio.get_event_loop()

    async def fake_push(frame, direction=None):
        timestamps.append(loop.time())

    proc.push_frame = fake_push  # type: ignore[method-assign]
    pcm = pcm_factory()
    proc.enqueue_cue(pcm)
    expected_gap = len(pcm) / (24000 * 2) + 0.2
    await asyncio.sleep(expected_gap + 0.2)

    assert len(timestamps) == 3
    actual_gap = timestamps[2] - timestamps[1]
    # The middle audio push and the trailing IndicationCueFrame are spaced
    # by roughly expected_gap. Allow ±50ms scheduling slack on top of
    # expected because asyncio.sleep is best-effort.
    assert abs(actual_gap - expected_gap) < 0.10, (
        f"tail gap {actual_gap:.3f}s vs expected {expected_gap:.3f}s"
    )
    await proc.aclose()


async def test_queue_overflow_drops_and_increments_counter() -> None:
    proc = build_sound_cue_processor(sample_rate=24000)
    pushed: list = []

    push_event = asyncio.Event()

    async def slow_push(frame, direction=None):
        # Block the very first push so the queue stays full.
        pushed.append(frame)
        await push_event.wait()

    proc.push_frame = slow_push  # type: ignore[method-assign]

    pcm = indication_assets.attention_cue(24000)
    proc.enqueue_cue(pcm)
    # Wait until drain has dequeued the first cue.
    for _ in range(50):
        if pushed:
            break
        await asyncio.sleep(0.01)

    # Queue is empty now (dequeued), but the drain task is awaiting push_event.
    # Enqueue 3 more — the first refills the queue, the next two should drop.
    proc.enqueue_cue(pcm)
    proc.enqueue_cue(pcm)
    proc.enqueue_cue(pcm)
    await asyncio.sleep(0.05)

    assert proc.cues_dropped == 2
    push_event.set()
    await proc.aclose()


async def test_processor_defers_while_bot_speaking_then_drops_after_2s() -> None:
    proc = build_sound_cue_processor(sample_rate=24000)
    captured = _patch_push_frame(proc)
    proc._bot_speaking = True  # simulate TTS in flight

    pcm = indication_assets.attention_cue(24000)
    proc.enqueue_cue(pcm)
    # Wait > 2s for the deferral horizon to expire while bot still speaks.
    await asyncio.sleep(2.2)
    assert captured == [], "cue must be dropped, not emitted, after 2s wait"
    assert proc.cues_dropped == 1
    await proc.aclose()


async def test_bot_started_stopped_frames_track_state() -> None:
    proc = build_sound_cue_processor(sample_rate=24000)
    # Bypass pipecat StartFrame requirement by stubbing super().process_frame.
    from pipecat.processors.frame_processor import FrameProcessor as _FP  # type: ignore

    async def _noop(self, frame, direction):
        return None

    orig = _FP.process_frame
    _FP.process_frame = _noop  # type: ignore[assignment]
    try:
        from pipecat.frames.frames import (  # type: ignore
            BotStartedSpeakingFrame,
            BotStoppedSpeakingFrame,
        )

        async def fake_push(frame, direction=None):
            pass

        proc.push_frame = fake_push  # type: ignore[method-assign]
        await proc.process_frame(BotStartedSpeakingFrame(), None)
        assert proc._bot_speaking is True
        await proc.process_frame(BotStoppedSpeakingFrame(), None)
        assert proc._bot_speaking is False
    finally:
        _FP.process_frame = orig  # type: ignore[assignment]
    await proc.aclose()


async def test_sound_backend_routes_pcm_to_processor() -> None:
    proc = build_sound_cue_processor(sample_rate=24000)
    captured = _patch_push_frame(proc)
    backend = SoundBackend(processor=proc)

    await backend.fire(
        IndicationKind.CONFIRMATION_DEADLINE,
        IndicationLevel.ATTENTION,
        "t",
        "b",
        {},
    )
    await asyncio.sleep(0.5)

    assert len(captured) == 3
    # Audio bytes must match the pre-built attention cue at this SR.
    expected_pcm = indication_assets.attention_cue(24000)
    assert captured[1].audio == expected_pcm
    await backend.aclose()
