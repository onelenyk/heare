"""US-IND-A4a integration: SoundCueProcessor in a real pipecat pipeline.

This proves the cue's bracketed frame sequence flows through pipecat's frame
routing (not just mocked push_frame). Verifies the IndicationCueFrame contract
that upstream consumers will use to suppress STT echo (full upstream gating
wired in US-IND-A5).
"""
from __future__ import annotations

import asyncio

from src import indication_assets
from src.indication import IndicationCueFrame, build_sound_cue_processor


async def test_sound_cue_propagates_through_real_pipeline() -> None:
    from pipecat.frames.frames import (  # type: ignore
        EndFrame,
        OutputAudioRawFrame,
    )
    from pipecat.pipeline.base_task import PipelineTaskParams  # type: ignore
    from pipecat.pipeline.pipeline import Pipeline  # type: ignore
    from pipecat.pipeline.task import PipelineTask  # type: ignore
    from pipecat.processors.frame_processor import (  # type: ignore
        FrameProcessor,
    )

    captured: list = []

    class CaptureProcessor(FrameProcessor):  # type: ignore[misc,valid-type]
        async def process_frame(self, frame, direction) -> None:  # type: ignore[override]
            await super().process_frame(frame, direction)
            captured.append(frame)
            await self.push_frame(frame, direction)

    sound_cue = build_sound_cue_processor(sample_rate=24000)
    sink = CaptureProcessor()
    pipeline = Pipeline([sound_cue, sink])
    task = PipelineTask(pipeline)

    # Spawn the task runner.
    params = PipelineTaskParams(loop=asyncio.get_running_loop())
    runner = asyncio.create_task(task.run(params))
    await asyncio.sleep(0.1)  # let StartFrame propagate

    pcm = indication_assets.attention_cue(24000)
    sound_cue.enqueue_cue(pcm)
    # Wait for cue duration + tail pad + scheduling slack.
    await asyncio.sleep(0.6)

    await task.queue_frames([EndFrame()])
    try:
        await asyncio.wait_for(runner, timeout=2.0)
    except asyncio.TimeoutError:
        runner.cancel()

    # Captured frames will include the StartFrame and our 3 cue frames.
    cue_starts = [
        f for f in captured if isinstance(f, IndicationCueFrame) and f.start
    ]
    cue_ends = [
        f for f in captured if isinstance(f, IndicationCueFrame) and not f.start
    ]
    audio_frames = [f for f in captured if isinstance(f, OutputAudioRawFrame)]

    assert len(cue_starts) == 1, f"start frames captured: {len(cue_starts)}"
    assert len(audio_frames) == 1, f"audio frames captured: {len(audio_frames)}"
    assert len(cue_ends) == 1, f"end frames captured: {len(cue_ends)}"
    assert audio_frames[0].audio == pcm
    assert audio_frames[0].sample_rate == 24000
    # Bare AudioRawFrame must not be the type — must be OutputAudioRawFrame
    # (the only routable subclass through transport.output()).
    assert type(audio_frames[0]) is OutputAudioRawFrame

    # Sequence ordering: start frame precedes audio precedes end frame.
    start_idx = captured.index(cue_starts[0])
    audio_idx = captured.index(audio_frames[0])
    end_idx = captured.index(cue_ends[0])
    assert start_idx < audio_idx < end_idx
