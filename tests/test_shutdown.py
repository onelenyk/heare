"""BCDE-004: run_until_stopped cancels pipeline runner on stop event."""
from __future__ import annotations

import asyncio
import signal

from src.main import run_until_stopped


class SleepingRunner:
    def __init__(self) -> None:
        self.started = False
        self.cancelled = False

    async def run(self, pipeline) -> None:
        self.started = True
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class SleepingHeartbeat:
    def __init__(self) -> None:
        self.stopped = False
        self.cancelled = False

    async def run(self) -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    def stop(self) -> None:
        self.stopped = True


async def test_run_until_stopped_cancels_on_sigterm() -> None:
    runner = SleepingRunner()
    heartbeat = SleepingHeartbeat()

    async def trigger() -> None:
        await asyncio.sleep(0.05)
        signal.raise_signal(signal.SIGTERM)

    trigger_task = asyncio.create_task(trigger())
    await asyncio.wait_for(
        run_until_stopped(runner, pipeline=object(), heartbeat=heartbeat),
        timeout=2.0,
    )
    await trigger_task
    assert runner.started is True
    assert runner.cancelled is True
    assert heartbeat.cancelled is True
    assert heartbeat.stopped is True


async def test_run_until_stopped_cancels_when_pipeline_exits() -> None:
    class QuickRunner:
        async def run(self, pipeline) -> None:
            return

    heartbeat = SleepingHeartbeat()
    await asyncio.wait_for(
        run_until_stopped(QuickRunner(), object(), heartbeat),
        timeout=2.0,
    )
    assert heartbeat.cancelled or heartbeat.stopped


async def test_run_until_stopped_awaits_decider_shutdown() -> None:
    """DP-002: run_until_stopped must await decider.shutdown() during teardown
    so the fire-and-forget emit drainer is cancelled cleanly instead of being
    GC'd with in-flight events on the queue."""

    class QuickRunner:
        async def run(self, pipeline) -> None:
            return

    class FakeDecider:
        def __init__(self) -> None:
            self.shutdown_called = 0

        async def shutdown(self) -> None:
            self.shutdown_called += 1

    heartbeat = SleepingHeartbeat()
    decider = FakeDecider()
    await asyncio.wait_for(
        run_until_stopped(
            QuickRunner(), object(), heartbeat, decider=decider
        ),
        timeout=2.0,
    )
    assert decider.shutdown_called == 1


async def test_run_until_stopped_survives_decider_shutdown_failure() -> None:
    """A broken decider.shutdown() must not crash teardown — the try/except
    in run_until_stopped swallows and logs the failure so the signal-handler
    removal and cancellation still complete."""

    class QuickRunner:
        async def run(self, pipeline) -> None:
            return

    class FailingDecider:
        async def shutdown(self) -> None:
            raise RuntimeError("boom during shutdown")

    heartbeat = SleepingHeartbeat()
    await asyncio.wait_for(
        run_until_stopped(
            QuickRunner(), object(), heartbeat, decider=FailingDecider()
        ),
        timeout=2.0,
    )
    assert heartbeat.stopped
