"""The watchdog: silence is stuck, and a task must end."""

from __future__ import annotations

import asyncio
import time

import pytest

from heare.reach.life import LifeOver, guarded


async def test_work_that_finishes_comes_back():
    async def quick() -> str:
        return "ok"

    assert await guarded(quick(), time.monotonic, idle_s=10, total_s=10) == "ok"


async def test_silence_is_stopped_as_idle():
    async def silent() -> None:
        await asyncio.sleep(10)

    started = time.monotonic()
    with pytest.raises(LifeOver, match="silent"):
        await guarded(silent(), lambda: started, idle_s=0.1, total_s=10)


async def test_a_long_task_is_stopped_as_too_long():
    async def slow() -> None:
        await asyncio.sleep(10)

    with pytest.raises(LifeOver, match="too long"):
        await guarded(slow(), time.monotonic, idle_s=10, total_s=0.1)


async def test_stop_is_told_on_the_way_out():
    stopped: list[bool] = []

    async def silent() -> None:
        await asyncio.sleep(10)

    async def stop() -> None:
        stopped.append(True)

    started = time.monotonic()
    with pytest.raises(LifeOver):
        await guarded(silent(), lambda: started, idle_s=0.1, total_s=10, stop=stop)
    assert stopped == [True]
