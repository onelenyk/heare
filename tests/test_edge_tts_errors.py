"""BCDE-006: edge-tts error handling (general Exception + transcode failures)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pipecat.services.tts_service")
pytest.importorskip("edge_tts")

import src.tts_edge as tts_mod  # noqa: E402
from src.tts_edge import create_edge_tts_service  # noqa: E402


class _FailingCommunicate:
    def __init__(self, text: str, voice: str) -> None:
        self.text = text
        self.voice = voice

    async def stream(self):
        raise RuntimeError("simulated dns failure")
        yield  # make this an async generator for type checker


async def test_run_tts_communicate_failure_yields_only_stopped() -> None:
    tts_mod._edge_tts_cls = None
    service = create_edge_tts_service(voice="uk-UA-PolinaNeural")
    frames = []
    with patch("edge_tts.Communicate", _FailingCommunicate):
        async for f in service.run_tts("привіт"):
            frames.append(f)
    assert len(frames) == 1
    assert type(frames[0]).__name__ == "TTSStoppedFrame"


class _PartialCommunicate:
    def __init__(self, text: str, voice: str) -> None:
        self.text = text
        self.voice = voice

    async def stream(self):
        yield {"type": "audio", "data": b"\x00" * 32}
        raise ConnectionResetError("socket died")


async def test_run_tts_mid_stream_failure_yields_stopped_only() -> None:
    tts_mod._edge_tts_cls = None
    service = create_edge_tts_service()
    frames = []
    with patch("edge_tts.Communicate", _PartialCommunicate):
        async for f in service.run_tts("тест"):
            frames.append(f)
    assert len(frames) == 1
    assert type(frames[0]).__name__ == "TTSStoppedFrame"


async def test_run_tts_transcode_failure_yields_stopped() -> None:
    tts_mod._edge_tts_cls = None
    service = create_edge_tts_service()

    class _OKCommunicate:
        def __init__(self, text, voice):
            pass

        async def stream(self):
            yield {"type": "audio", "data": b"FAKE"}

    with patch("edge_tts.Communicate", _OKCommunicate), patch.object(
        tts_mod,
        "_mp3_to_pcm_s16le",
        AsyncMock(side_effect=RuntimeError("ffmpeg broke")),
    ):
        frames = [f async for f in service.run_tts("привіт")]
    assert len(frames) == 1
    assert type(frames[0]).__name__ == "TTSStoppedFrame"
