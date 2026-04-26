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


async def test_run_until_stopped_cancels_on_sigterm() -> None:
    runner = SleepingRunner()

    async def trigger() -> None:
        await asyncio.sleep(0.05)
        signal.raise_signal(signal.SIGTERM)

    trigger_task = asyncio.create_task(trigger())
    await asyncio.wait_for(
        run_until_stopped(runner, pipeline=object()),
        timeout=2.0,
    )
    await trigger_task
    assert runner.started is True
    assert runner.cancelled is True


async def test_run_until_stopped_cancels_when_pipeline_exits() -> None:
    class QuickRunner:
        async def run(self, pipeline) -> None:
            return

    await asyncio.wait_for(
        run_until_stopped(QuickRunner(), object()),
        timeout=2.0,
    )


async def test_run_until_stopped_awaits_decider_shutdown() -> None:
    class QuickRunner:
        async def run(self, pipeline) -> None:
            return

    class FakeDecider:
        def __init__(self) -> None:
            self.shutdown_called = 0

        async def shutdown(self) -> None:
            self.shutdown_called += 1

    decider = FakeDecider()
    await asyncio.wait_for(
        run_until_stopped(
            QuickRunner(), object(), decider=decider
        ),
        timeout=2.0,
    )
    assert decider.shutdown_called == 1


async def test_run_until_stopped_survives_decider_shutdown_failure() -> None:
    class QuickRunner:
        async def run(self, pipeline) -> None:
            return

    class FailingDecider:
        async def shutdown(self) -> None:
            raise RuntimeError("boom during shutdown")

    await asyncio.wait_for(
        run_until_stopped(
            QuickRunner(), object(), decider=FailingDecider()
        ),
        timeout=2.0,
    )
