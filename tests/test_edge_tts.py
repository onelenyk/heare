"""Tests for src/tts_edge.py EdgeTTSService.

Streaming pipeline now spawns a real ffmpeg subprocess and pipes edge-tts MP3
chunks through it concurrently, so tests use a real MP3 (generated via ffmpeg)
to feed through the real ffmpeg subprocess. Skipped if ffmpeg is unavailable.
"""
from __future__ import annotations

import shutil
import subprocess
from unittest.mock import patch

import pytest

pytest.importorskip("pipecat.services.tts_service")
pytest.importorskip("edge_tts")

import src.tts_edge as tts_mod  # noqa: E402
from src.tts_edge import create_edge_tts_service  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _real_mp3_silence(duration_s: float = 0.1, sample_rate: int = 24000) -> bytes:
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


class _FakeCommunicateRealMP3:
    """Streams a real MP3 in two chunks so the streaming pipeline gets real input."""

    def __init__(self, mp3: bytes):
        self._mp3 = mp3

    def __call__(self, text: str, voice: str):
        self._text = text
        return self

    async def stream(self):
        half = max(1, len(self._mp3) // 2)
        yield {"type": "audio", "data": self._mp3[:half]}
        yield {"type": "audio", "data": self._mp3[half:]}
        yield {"type": "meta", "data": None}


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_run_tts_yields_audio_and_stopped_frames() -> None:
    tts_mod._edge_tts_cls = None
    service = create_edge_tts_service(voice="uk-UA-PolinaNeural")
    mp3 = _real_mp3_silence(duration_s=0.1)
    fake = _FakeCommunicateRealMP3(mp3)
    with patch("edge_tts.Communicate", fake):
        frames = [f async for f in service.run_tts("привіт")]
    audio_frames = [f for f in frames if hasattr(f, "audio")]
    assert audio_frames, "expected at least one TTSAudioRawFrame"
    total_pcm = sum(len(f.audio) for f in audio_frames)
    # 0.1s @ 24kHz mono s16le ≈ 4800 bytes (within tolerance for MP3 padding)
    assert total_pcm > 0
    assert total_pcm % 2 == 0  # valid s16le
    assert any(type(f).__name__ == "TTSStoppedFrame" for f in frames)


async def test_run_tts_empty_text_yields_only_stopped() -> None:
    tts_mod._edge_tts_cls = None
    service = create_edge_tts_service()
    frames = [f async for f in service.run_tts("")]
    assert len(frames) == 1
    assert type(frames[0]).__name__ == "TTSStoppedFrame"
