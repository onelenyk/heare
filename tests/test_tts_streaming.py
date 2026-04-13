"""Verifies that EdgeTTSService streams audio frames concurrently with MP3 download.

The key invariant: first TTSAudioRawFrame must be yielded BEFORE the last MP3
chunk is consumed from edge-tts. This proves the streaming pipeline (MP3 →
ffmpeg.stdin → ffmpeg.stdout → PCM frames) is actually concurrent, not the
old buffer-then-transcode pattern.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from unittest.mock import patch

import pytest

pytest.importorskip("pipecat.services.tts_service")
pytest.importorskip("edge_tts")

import src.tts_edge as tts_mod  # noqa: E402
from src.tts_edge import create_edge_tts_service  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _real_mp3(duration_s: float = 2.0, sample_rate: int = 24000) -> bytes:
    """Generate a real MP3 with a 440Hz tone (not silence — silence compresses
    too much and ffmpeg may buffer it entirely before flushing PCM)."""
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"sine=frequency=440:sample_rate={sample_rate}:duration={duration_s}",
            "-acodec", "libmp3lame",
            "-f", "mp3", "pipe:1",
        ],
        capture_output=True,
    )
    assert proc.returncode == 0
    return proc.stdout


class _SlowStreamingCommunicate:
    """Streams MP3 in many small chunks with sleeps between, recording when each is sent."""

    def __init__(self, mp3: bytes, num_chunks: int = 8, delay_s: float = 0.05):
        self._mp3 = mp3
        self._num_chunks = num_chunks
        self._delay = delay_s
        self.sent_at: list[float] = []

    def __call__(self, text: str, voice: str):
        return self

    async def stream(self):
        loop = asyncio.get_event_loop()
        chunk_size = max(1, len(self._mp3) // self._num_chunks)
        for i in range(0, len(self._mp3), chunk_size):
            self.sent_at.append(loop.time())
            yield {"type": "audio", "data": self._mp3[i:i + chunk_size]}
            await asyncio.sleep(self._delay)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_first_audio_frame_arrives_before_last_mp3_chunk() -> None:
    """The streaming pipeline must yield PCM before all MP3 chunks are consumed.

    With ~10 MP3 chunks at 100ms apart (~1s total feed time), the streaming
    pipeline should produce the first PCM frame well before the feed completes.
    """
    tts_mod._edge_tts_cls = None
    service = create_edge_tts_service(sample_rate=24000)

    # Use a 2-second tone (not silence) so MP3 is large enough that ffmpeg
    # has to flush PCM mid-stream rather than buffer everything.
    mp3 = _real_mp3(duration_s=2.0)
    fake = _SlowStreamingCommunicate(mp3, num_chunks=10, delay_s=0.1)

    loop = asyncio.get_event_loop()
    first_frame_at: float | None = None
    audio_frame_count = 0

    with patch("edge_tts.Communicate", fake):
        async for frame in service.run_tts("test"):
            if hasattr(frame, "audio") and first_frame_at is None:
                first_frame_at = loop.time()
            if hasattr(frame, "audio"):
                audio_frame_count += 1

    assert audio_frame_count > 0, "expected audio frames"
    assert first_frame_at is not None
    assert len(fake.sent_at) >= 10

    # The first audio frame must arrive BEFORE the last MP3 chunk was sent.
    # This proves the pipeline is genuinely concurrent: ffmpeg produced PCM
    # while edge-tts was still streaming MP3 chunks in.
    last_mp3_sent = fake.sent_at[-1]
    assert first_frame_at < last_mp3_sent, (
        f"first audio frame at {first_frame_at:.3f}s but last MP3 chunk sent at "
        f"{last_mp3_sent:.3f}s — pipeline is buffering, not streaming"
    )


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_streaming_yields_multiple_audio_frames_for_long_input() -> None:
    """A 1-second MP3 should produce multiple audio frames (not one giant blob)."""
    tts_mod._edge_tts_cls = None
    service = create_edge_tts_service(sample_rate=24000)

    mp3 = _real_mp3(duration_s=1.0)

    class _OneShotCommunicate:
        def __call__(self, text, voice):
            return self

        async def stream(self):
            yield {"type": "audio", "data": mp3}

    with patch("edge_tts.Communicate", _OneShotCommunicate()):
        frames = [f async for f in service.run_tts("hello world")]

    audio_frames = [f for f in frames if hasattr(f, "audio")]
    # 1s @ 24kHz s16le mono = 48000 bytes; chunk size = 4800 (100ms) → ~10 chunks
    assert len(audio_frames) >= 5, (
        f"expected multiple chunked audio frames, got {len(audio_frames)}"
    )
    assert type(frames[-1]).__name__ == "TTSStoppedFrame"


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_streaming_handles_edge_tts_failure_gracefully() -> None:
    """If edge-tts raises mid-stream, ffmpeg.stdin closes cleanly and we get TTSStoppedFrame."""
    tts_mod._edge_tts_cls = None
    service = create_edge_tts_service()

    class _FailingCommunicate:
        def __call__(self, text, voice):
            return self

        async def stream(self):
            # Yield one chunk of garbage so ffmpeg starts then fail
            yield {"type": "audio", "data": b"\x00" * 100}
            raise RuntimeError("simulated network error")

    with patch("edge_tts.Communicate", _FailingCommunicate()):
        frames = [f async for f in service.run_tts("тест")]

    # Must end with TTSStoppedFrame regardless
    assert len(frames) >= 1
    assert type(frames[-1]).__name__ == "TTSStoppedFrame"
