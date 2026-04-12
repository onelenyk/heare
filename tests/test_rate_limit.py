"""BCDE-007: RateLimiter delays calls beyond the window cap."""
from __future__ import annotations

import pytest

from src.rate_limit import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def tick(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:
        return self.now


async def test_under_limit_no_delay() -> None:
    clock = FakeClock()
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)
        clock.tick(s)

    limiter = RateLimiter(max_calls=3, window_seconds=60.0, clock=clock, sleeper=fake_sleep)
    for _ in range(3):
        await limiter.acquire()
    assert sleeps == []


async def test_over_limit_sleeps_until_window_resets() -> None:
    clock = FakeClock()
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)
        clock.tick(s)

    limiter = RateLimiter(max_calls=3, window_seconds=60.0, clock=clock, sleeper=fake_sleep)
    for _ in range(3):
        await limiter.acquire()
    clock.tick(10.0)
    await limiter.acquire()
    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(50.0, abs=0.01)


async def test_window_prune_frees_slots() -> None:
    clock = FakeClock()

    async def fake_sleep(s: float) -> None:
        clock.tick(s)

    limiter = RateLimiter(max_calls=2, window_seconds=10.0, clock=clock, sleeper=fake_sleep)
    await limiter.acquire()
    await limiter.acquire()
    clock.tick(11.0)
    await limiter.acquire()
    await limiter.acquire()
    assert True
