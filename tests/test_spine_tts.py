"""Tests for src/spine/tts.py: standalone edge-tts streaming synthesis.

Mirrors the pattern in tests/test_edge_tts.py — a real MP3 (silence,
generated via ffmpeg) is streamed through a fake edge_tts.Communicate and
piped through the real ffmpeg subprocess so the transcode path is
exercised for real, without any network calls or a pipecat dependency.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from unittest.mock import patch

import pytest

pytest.importorskip("edge_tts")

from src.spine.tts import synthesise  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _real_mp3_silence(duration_s: float = 0.2, sample_rate: int = 24000) -> bytes:
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"anullsrc=r={sample_rate}:cl=mono",
            "-t", str(duration_s),
            "-acodec", "libmp3lame",
            "-f", "mp3", "pipe:1",
        ],
        capture_output=True,
    )
    assert proc.returncode == 0
    return proc.stdout


class _FakeCommunicate:
    """Streams a real MP3 in two chunks + a non-audio meta message."""

    def __init__(self, mp3: bytes):
        self._mp3 = mp3

    def __call__(self, text: str, voice: str):
        self._text = text
        self._voice = voice
        return self

    async def stream(self):
        half = max(1, len(self._mp3) // 2)
        yield {"type": "audio", "data": self._mp3[:half]}
        yield {"type": "audio", "data": self._mp3[half:]}
        yield {"type": "meta", "data": None}


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_synthesise_yields_pcm_chunks() -> None:
    mp3 = _real_mp3_silence(duration_s=0.2)
    fake = _FakeCommunicate(mp3)
    chunks = []
    with patch("edge_tts.Communicate", fake):
        async for chunk in synthesise("привіт", sample_rate=24000):
            chunks.append(chunk)
    assert len(chunks) >= 1
    total = sum(len(c) for c in chunks)
    assert total > 0
    assert total % 2 == 0  # valid s16le
    # 0.2s @ 24kHz mono s16le = 9600 bytes; generous tolerance for MP3 padding.
    assert 4000 <= total <= 30000, f"unexpected total PCM size: {total}"


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_synthesise_empty_text_yields_nothing_and_skips_communicate() -> None:
    with patch("edge_tts.Communicate") as mock_communicate:
        chunks = [c async for c in synthesise("   ")]
    assert chunks == []
    mock_communicate.assert_not_called()


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_synthesise_cancel_mid_stream_leaves_no_process_running() -> None:
    mp3 = _real_mp3_silence(duration_s=0.2)
    fake = _FakeCommunicate(mp3)

    created_procs = []
    real_create_subprocess_exec = asyncio.create_subprocess_exec

    async def _tracking_create_subprocess_exec(*args, **kwargs):
        proc = await real_create_subprocess_exec(*args, **kwargs)
        created_procs.append(proc)
        return proc

    with patch("edge_tts.Communicate", fake), patch(
        "asyncio.create_subprocess_exec", _tracking_create_subprocess_exec
    ):
        gen = synthesise("привіт", sample_rate=24000)
        async for _chunk in gen:
            break
        await gen.aclose()

    assert created_procs, "expected an ffmpeg subprocess to have been created"
    for proc in created_procs:
        assert proc.returncode is not None, "ffmpeg process leaked after cancel"


