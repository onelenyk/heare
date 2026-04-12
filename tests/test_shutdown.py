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
