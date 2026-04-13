"""Tests for src/heartbeat.py WarmupTask."""
from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, patch

import pytest

from src.heartbeat import WarmupTask


@pytest.fixture(autouse=True)
def short_warmup_floor():
    """Lower the production floor so tests can use millisecond intervals."""
    with patch.object(WarmupTask, "_MIN_INTERVAL_SECONDS", 0.01):
        yield


@pytest.fixture
def fake_edge_tts():
    """Inject a fake edge_tts module so we don't hit the network."""
    fake_mod = types.ModuleType("edge_tts")

    class _FakeCommunicate:
        instances: list = []

        def __init__(self, text, voice):
            self.text = text
            self.voice = voice
            type(self).instances.append(self)

        async def stream(self):
            yield {"type": "audio", "data": b"\x00"}
            yield {"type": "meta"}

    fake_mod.Communicate = _FakeCommunicate  # type: ignore[attr-defined]
    fake_mod.exceptions = types.ModuleType("edge_tts.exceptions")  # type: ignore[attr-defined]

    with patch.dict(sys.modules, {"edge_tts": fake_mod, "edge_tts.exceptions": fake_mod.exceptions}):
        yield _FakeCommunicate


async def test_warmup_task_pings_edge_tts(fake_edge_tts) -> None:
    """One run() iteration should call edge_tts.Communicate exactly once."""
    fake_edge_tts.instances.clear()
    task = WarmupTask(voice="uk-UA-PolinaNeural", interval_seconds=0.05)

    runner = asyncio.create_task(task.run())
    # Yield repeatedly so the runner actually gets event-loop time
    for _ in range(20):
        await asyncio.sleep(0.02)
        if len(fake_edge_tts.instances) >= 1:
            break
    task.stop()
    await runner

    assert len(fake_edge_tts.instances) >= 1
    first = fake_edge_tts.instances[0]
    assert first.text == "."
    assert first.voice == "uk-UA-PolinaNeural"


async def test_warmup_task_handles_exception() -> None:
    """A failing ping must NOT crash the task — next iteration still runs."""
    task = WarmupTask(voice="x", interval_seconds=0.05)

    with patch.object(
        task, "_ping_edge_tts", AsyncMock(side_effect=RuntimeError("network down"))
    ):
        runner = asyncio.create_task(task.run())
        await asyncio.sleep(0.12)
        task.stop()
        await runner  # should not raise


async def test_warmup_task_can_be_stopped() -> None:
    """stop() must cleanly cancel the loop."""
    task = WarmupTask(voice="x", interval_seconds=10.0)  # long interval

    with patch.object(task, "_ping_edge_tts", AsyncMock()):
        runner = asyncio.create_task(task.run())
        await asyncio.sleep(0.05)  # let it enter the wait
        task.stop()
        # Should return promptly, not wait the full 10s
        await asyncio.wait_for(runner, timeout=1.0)


async def test_warmup_task_respects_interval() -> None:
    """Two ticks should be roughly interval-seconds apart, not back-to-back."""
    call_times: list[float] = []
    loop = asyncio.get_event_loop()

    async def record_ping():
        call_times.append(loop.time())

    task = WarmupTask(voice="x", interval_seconds=0.08)
    with patch.object(task, "_ping_edge_tts", record_ping):
        runner = asyncio.create_task(task.run())
        # Yield repeatedly until we collect at least 2 calls
        for _ in range(40):
            await asyncio.sleep(0.02)
            if len(call_times) >= 2:
                break
        task.stop()
        await runner

    assert len(call_times) >= 2, f"expected ≥2 ticks, got {len(call_times)}"
    gaps = [b - a for a, b in zip(call_times, call_times[1:])]
    # Each gap should be at least ~0.06s (allow scheduler slack vs the 0.08 interval)
    for gap in gaps:
        assert gap >= 0.05, f"warmup ticks too close: {gap}s"


def test_warmup_task_uses_settings_default() -> None:
    """The default interval should match Settings.warmup_interval_seconds."""
    from src.config import Settings

    s = Settings()
    assert s.warmup_interval_seconds == 240.0
    task = WarmupTask(voice="x", interval_seconds=s.warmup_interval_seconds)
    assert task.interval_seconds == 240.0
