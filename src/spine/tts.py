"""Standalone edge-tts streaming synthesis: text -> raw PCM chunks.

edge_tts.Communicate streams MP3; an ffmpeg subprocess transcodes it to
raw PCM (s16le mono) concurrently. A writer task feeds ffmpeg's stdin as
MP3 bytes arrive from edge-tts, while this generator reads ffmpeg's
stdout and yields PCM chunks as they become available — the first audio
chunk does not wait for the last MP3 byte.

Adapted from the streaming + fade approach in src/voice/tts/edge.py, but
with no pipecat dependency: no frame wrapping, no TTSService subclass.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import AsyncIterator

logger = logging.getLogger("spine.tts")

_CHUNK_SIZE = 4096


async def synthesise(
    text: str,
    *,
    voice: str = "uk-UA-PolinaNeural",
    sample_rate: int = 24000,
) -> AsyncIterator[bytes]:
    """Yield raw PCM (s16le mono, sample_rate) chunks for `text`.

    edge_tts.Communicate streams MP3; an ffmpeg subprocess
    (-i pipe:0 -f s16le -ac 1 -ar {rate} pipe:1) transcodes concurrently:
    a writer task feeds stdin as MP3 arrives, the generator yields stdout
    chunks — first audio must not wait for the last MP3 byte. Empty or
    whitespace-only text yields nothing. ffmpeg missing raises
    RuntimeError with a clear message. On generator close/cancellation the
    subprocess is terminated, never leaked.
    """
    text = (text or "").strip()
    if not text:
        return

    import edge_tts

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError(
            "ffmpeg not found on PATH: required to transcode edge-tts MP3 "
            "output to PCM"
        )

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
        stderr=asyncio.subprocess.DEVNULL,
    )

    assert proc.stdin is not None
    assert proc.stdout is not None

    async def feed_mp3() -> None:
        try:
            communicate = edge_tts.Communicate(text, voice)
            async for chunk in communicate.stream():
                if chunk.get("type") != "audio":
                    continue
                data = chunk.get("data")
                if not data:
                    continue
                try:
                    proc.stdin.write(data)
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    return
        except edge_tts.exceptions.NoAudioReceived:
            pass
        finally:
            if proc.stdin is not None and not proc.stdin.is_closing():
                try:
                    proc.stdin.close()
                except Exception:
                    pass

    feed_task = asyncio.create_task(feed_mp3())

    try:
        while True:
            data = await proc.stdout.read(_CHUNK_SIZE)
            if not data:
                break
            yield data
    finally:
        # Reached on normal completion, on early `break`/return from the
        # caller's `async for`, and on `aclose()`/cancellation. Ensure the
        # ffmpeg subprocess and the feeder task never outlive the generator.
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        feed_task.cancel()
        try:
            results = await asyncio.wait_for(
                asyncio.gather(feed_task, return_exceptions=True), timeout=2.0
            )
        except asyncio.TimeoutError:
            pass
        else:
            # A feeder failure closes ffmpeg's stdin cleanly, so the reader
            # sees a normal EOF and truncated speech would otherwise leave
            # no trace of why.
            for r in results:
                if isinstance(r, Exception) and not isinstance(
                    r, asyncio.CancelledError
                ):
                    logger.warning("tts feeder failed mid-stream: %r", r)
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
