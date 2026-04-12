"""Tests for src/tts_edge.py EdgeTTSService.

Skipped if pipecat is not importable. Patches edge_tts.Communicate AND the
_mp3_to_pcm_s16le helper so we don't hit the network and don't need ffmpeg.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pipecat.services.tts_service")
pytest.importorskip("edge_tts")

import src.tts_edge as tts_mod  # noqa: E402
from src.tts_edge import create_edge_tts_service  # noqa: E402


class _FakeCommunicate:
    def __init__(self, text: str, voice: str) -> None:
        self.text = text
        self.voice = voice

    async def stream(self):
        yield {"type": "audio", "data": b"FAKEMP3CHUNK1"}
        yield {"type": "audio", "data": b"FAKEMP3CHUNK2"}
        yield {"type": "meta", "data": None}


async def test_run_tts_yields_audio_and_stopped_frames() -> None:
    tts_mod._edge_tts_cls = None
    service = create_edge_tts_service(voice="uk-UA-PolinaNeural")
    fake_pcm = b"\x00\x01" * 24000  # 1 second of silence @24kHz s16 mono
    with patch("edge_tts.Communicate", _FakeCommunicate), patch.object(
        tts_mod, "_mp3_to_pcm_s16le", AsyncMock(return_value=fake_pcm)
    ):
        frames = [f async for f in service.run_tts("привіт")]
    audio_frames = [f for f in frames if hasattr(f, "audio")]
    assert audio_frames, "expected at least one TTSAudioRawFrame"
    assert sum(len(f.audio) for f in audio_frames) == len(fake_pcm)
    assert any(type(f).__name__ == "TTSStoppedFrame" for f in frames)


async def test_run_tts_empty_text_yields_only_stopped() -> None:
    tts_mod._edge_tts_cls = None
    service = create_edge_tts_service()
    frames = [f async for f in service.run_tts("")]
    assert len(frames) == 1
    assert type(frames[0]).__name__ == "TTSStoppedFrame"
