"""Teardown guarantees for ``_run_pipeline_loop``.

The menubar stops the voice pipeline by cancelling the task running
``_build_and_run_daemon``. If that cancellation skips the loop's teardown,
the pipecat runner keeps going — mic open, agent still speaking — while the
UI reports "stopped", and the next start stacks a second live pipeline on
top of the orphan. These tests pin the teardown down.
"""
from __future__ import annotations

import asyncio

import pytest

from src.main import _run_pipeline_loop


class _FakeRunner:
    """Stands in for pipecat's PipelineRunner."""

    def __init__(self) -> None:
        self.cancelled = False
        self.running = asyncio.Event()

    async def run(self, _pipeline) -> None:
        self.running.set()
        try:
            await asyncio.Event().wait()  # runs until cancelled
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def cancel(self) -> None:
        self.cancelled = True


@pytest.mark.asyncio
async def test_teardown_cancels_runner_when_loop_is_cancelled() -> None:
    runner = _FakeRunner()
    loop_task = asyncio.ensure_future(
        _run_pipeline_loop(runner, object(), handle_signals=False)
    )
    await asyncio.wait_for(runner.running.wait(), timeout=1)

    loop_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop_task

    assert runner.cancelled, "runner survived cancellation — orphaned pipeline"


@pytest.mark.asyncio
async def test_teardown_drains_helper_tasks_on_cancellation() -> None:
    """Warmup and bridge tasks must not outlive the loop either."""
    runner = _FakeRunner()

    async def _forever() -> None:
        await asyncio.Event().wait()

    class _Warmup:
        def __init__(self) -> None:
            self.task_started = asyncio.Event()

        async def run(self) -> None:
            self.task_started.set()
            await _forever()

    warmup = _Warmup()
    bridge_task = asyncio.ensure_future(_forever())

    loop_task = asyncio.ensure_future(
        _run_pipeline_loop(
            runner, object(), warmup, bridge_task=bridge_task, handle_signals=False
        )
    )
    await asyncio.wait_for(runner.running.wait(), timeout=1)
    await asyncio.wait_for(warmup.task_started.wait(), timeout=1)

    loop_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop_task

    assert runner.cancelled
    await asyncio.sleep(0)
    assert bridge_task.done(), "bridge task leaked past teardown"


@pytest.mark.asyncio
async def test_normal_completion_still_tears_down() -> None:
    """When the pipeline ends on its own, teardown runs the same way."""

    class _SelfEndingRunner(_FakeRunner):
        async def run(self, _pipeline) -> None:
            self.running.set()
            return  # completes immediately

    runner = _SelfEndingRunner()
    await asyncio.wait_for(
        _run_pipeline_loop(runner, object(), handle_signals=False), timeout=2
    )
    assert runner.cancelled
