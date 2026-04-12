"""Pipecat TTSService wrapping edge-tts for Ukrainian output.

edge-tts streams MP3; Pipecat's LocalAudioOutputTransport expects raw PCM
(signed 16-bit, little endian). We transcode via an ffmpeg subprocess: feed
the full MP3 buffer in, read PCM out, emit in fixed-size TTSAudioRawFrame
chunks.

Pipecat + edge-tts imports are deferred so the module can be imported in
tests without pulling the full stack.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Any, AsyncGenerator

logger = logging.getLogger("heare.tts")

_edge_tts_cls = None


async def _mp3_to_pcm_s16le(mp3_bytes: bytes, sample_rate: int) -> bytes:
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    proc = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "mp3",
        "-i",
        "pipe:0",
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(input=mp3_bytes)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed ({proc.returncode}): {err.decode(errors='replace')[:200]}"
        )
    return out


def _build_edge_tts_class():
    global _edge_tts_cls
    if _edge_tts_cls is not None:
        return _edge_tts_cls
    import edge_tts
    from pipecat.frames.frames import Frame, TTSAudioRawFrame, TTSStoppedFrame
    from pipecat.services.settings import TTSSettings
    from pipecat.services.tts_service import TTSService
    from pipecat.transcriptions.language import Language

    class EdgeTTSService(TTSService):  # type: ignore[misc]
        def __init__(
            self,
            *,
            voice: str = "uk-UA-PolinaNeural",
            sample_rate: int = 24000,
            **kwargs: Any,
        ) -> None:
            super().__init__(
                sample_rate=sample_rate,
                settings=TTSSettings(
                    model=None,
                    voice=voice,
                    language=Language.UK,
                ),
                **kwargs,
            )
            self._voice = voice
            self._sample_rate = sample_rate

        async def run_tts(
            self, text: str, context_id: str | None = None
        ) -> AsyncGenerator[Frame, None]:
            text = (text or "").strip()
            if not text:
                yield TTSStoppedFrame()
                return
            mp3 = bytearray()
            try:
                communicate = edge_tts.Communicate(text, self._voice)
                async for chunk in communicate.stream():
                    if chunk.get("type") != "audio":
                        continue
                    data = chunk.get("data")
                    if data:
                        mp3.extend(data)
            except edge_tts.exceptions.NoAudioReceived:
                logger.warning("edge-tts returned no audio for text: %r", text[:80])
                yield TTSStoppedFrame()
                return
            except Exception as e:
                logger.exception("edge-tts stream failed: %s", e)
                yield TTSStoppedFrame()
                return

            if not mp3:
                yield TTSStoppedFrame()
                return

            try:
                pcm = await _mp3_to_pcm_s16le(bytes(mp3), self._sample_rate)
            except Exception as e:
                logger.exception("mp3 → pcm transcode failed: %s", e)
                yield TTSStoppedFrame()
                return

            logger.info(
                "edge-tts produced %d MP3 bytes -> %d PCM bytes @ %dHz",
                len(mp3),
                len(pcm),
                self._sample_rate,
            )

            chunk_size = self._sample_rate * 2 // 10  # ~100ms of 16-bit mono
            for i in range(0, len(pcm), chunk_size):
                yield TTSAudioRawFrame(
                    audio=pcm[i : i + chunk_size],
                    sample_rate=self._sample_rate,
                    num_channels=1,
                )
            yield TTSStoppedFrame()

    _edge_tts_cls = EdgeTTSService
    return EdgeTTSService


def create_edge_tts_service(
    *,
    voice: str = "uk-UA-PolinaNeural",
    sample_rate: int = 24000,
):
    cls = _build_edge_tts_class()
    return cls(voice=voice, sample_rate=sample_rate)
