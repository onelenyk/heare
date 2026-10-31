"""The synthesiser's process life, with edge-tts and ffmpeg faked.

No network, no external binary: what is tested is that an early exit from
the generator kills ffmpeg, stops its feeder and leaves nothing running.
"""

from __future__ import annotations

import asyncio
import sys
import types

from heare.channels.voice.tts import synthesise


class FakeStdin:
    def __init__(self) -> None:
        self.closed = False

    def write(self, data: bytes) -> None:
        pass

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def is_closing(self) -> bool:
        return self.closed


class FakeStdout:
    def __init__(self) -> None:
        self._chunks = [b"pcm-first"]

    async def read(self, size: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        await asyncio.Event().wait()
        return b""


class FakeProcess:
    def __init__(self) -> None:
        self.stdin = FakeStdin()
        self.stdout = FakeStdout()
        self.returncode: int | None = None
        self.killed = 0
        self.waited = 0

    def kill(self) -> None:
        self.killed += 1
        self.returncode = -9

    async def wait(self) -> int:
        self.waited += 1
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class FakeCommunicate:
    def __init__(self) -> None:
        self.finished = asyncio.Event()

    async def stream(self):
        try:
            yield {"type": "audio", "data": b"mp3"}
            await asyncio.Event().wait()
        finally:
            self.finished.set()


async def test_an_early_exit_kills_ffmpeg_and_its_feeder(monkeypatch):
    process = FakeProcess()
    communicate = FakeCommunicate()

    async def create(*args, **kwargs):
        return process

    fake_edge = types.SimpleNamespace(
        Communicate=lambda text, voice: communicate,
        exceptions=types.SimpleNamespace(NoAudioReceived=RuntimeError),
    )
    monkeypatch.setitem(sys.modules, "edge_tts", fake_edge)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)

    generator = synthesise("Привіт.")
    assert await generator.__anext__() == b"pcm-first"
    await asyncio.sleep(0)
    await generator.aclose()

    assert process.killed == 1, "ffmpeg was left running"
    assert process.waited >= 1, "the process was killed but not reaped"
    assert communicate.finished.is_set(), "the edge-tts feeder still runs"
    assert process.stdin.is_closing()
    leaked = [
        task for task in asyncio.all_tasks()
        if "feed_mp3" in getattr(task.get_coro(), "__qualname__", "")
    ]
    assert leaked == []
