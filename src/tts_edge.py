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
import time
from typing import TYPE_CHECKING, Any, AsyncGenerator

if TYPE_CHECKING:
    from .tts_cache import TTSCache

logger = logging.getLogger("heare.tts")

_edge_tts_cls = None


async def synthesize_to_pcm(text: str, voice: str, sample_rate: int) -> bytes:
    """One-shot synth: edge-tts → MP3 → ffmpeg → PCM bytes.

    Used by TTSCache.warmup at startup. Not on the hot path — the streaming
    pipeline in run_tts is what production calls use.
    """
    import edge_tts

    mp3 = bytearray()
    comm = edge_tts.Communicate(text, voice)
    async for chunk in comm.stream():
        if chunk.get("type") == "audio" and chunk.get("data"):
            mp3.extend(chunk["data"])
    if not mp3:
        return b""
    return await _mp3_to_pcm_s16le(bytes(mp3), sample_rate)


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
            cache: "TTSCache | None" = None,
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
            self._cache = cache

        async def run_tts(
            self, text: str, context_id: str | None = None
        ) -> AsyncGenerator[Frame, None]:
            text = (text or "").strip()
            if not text:
                yield TTSStoppedFrame()
                return

            chunk_size = self._sample_rate * 2 // 10  # ~100ms of 16-bit mono

            # Cache fast path: pre-rendered PCM for fixed phrases.
            if self._cache is not None:
                cached = self._cache.get(text)
                if cached:
                    t_cache = time.monotonic()
                    for i in range(0, len(cached), chunk_size):
                        yield TTSAudioRawFrame(
                            audio=cached[i : i + chunk_size],
                            sample_rate=self._sample_rate,
                            num_channels=1,
                        )
                    yield TTSStoppedFrame()
                    logger.info(
                        "[TIMING] tts CACHE HIT text=%r yielded %dB in %.0fms",
                        text[:40],
                        len(cached),
                        (time.monotonic() - t_cache) * 1000,
                    )
                    return

            ffmpeg = shutil.which("ffmpeg") or "ffmpeg"

            t0 = time.monotonic()
            t_first_mp3: float | None = None
            t_first_pcm: float | None = None
            mp3_bytes_total = 0
            pcm_bytes_total = 0

            try:
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
                    str(self._sample_rate),
                    "pipe:1",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except Exception as e:
                logger.exception("failed to launch ffmpeg: %s", e)
                yield TTSStoppedFrame()
                return

            pcm_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
            feed_error: list[BaseException] = []

            async def feed_mp3() -> None:
                nonlocal t_first_mp3, mp3_bytes_total
                try:
                    communicate = edge_tts.Communicate(text, self._voice)
                    async for chunk in communicate.stream():
                        if chunk.get("type") != "audio":
                            continue
                        data = chunk.get("data")
                        if not data:
                            continue
                        if t_first_mp3 is None:
                            t_first_mp3 = time.monotonic()
                        mp3_bytes_total += len(data)
                        try:
                            proc.stdin.write(data)
                            await proc.stdin.drain()
                        except (BrokenPipeError, ConnectionResetError):
                            return
                except edge_tts.exceptions.NoAudioReceived:
                    logger.warning(
                        "edge-tts returned no audio for text: %r", text[:80]
                    )
                except Exception as e:
                    feed_error.append(e)
                    logger.exception("edge-tts stream failed: %s", e)
                finally:
                    if proc.stdin and not proc.stdin.is_closing():
                        try:
                            proc.stdin.close()
                        except Exception:
                            pass

            async def drain_pcm() -> None:
                nonlocal t_first_pcm, pcm_bytes_total
                buf = bytearray()
                try:
                    while True:
                        # Read small to yield first chunk ASAP; ffmpeg buffers
                        # internally so this won't fragment the audio.
                        data = await proc.stdout.read(1024)
                        if not data:
                            break
                        if t_first_pcm is None:
                            t_first_pcm = time.monotonic()
                        pcm_bytes_total += len(data)
                        buf.extend(data)
                        while len(buf) >= chunk_size:
                            await pcm_queue.put(bytes(buf[:chunk_size]))
                            del buf[:chunk_size]
                    if buf:
                        await pcm_queue.put(bytes(buf))
                except Exception as e:
                    logger.exception("ffmpeg stdout drain failed: %s", e)
                finally:
                    await pcm_queue.put(None)

            feed_task = asyncio.create_task(feed_mp3())
            drain_task = asyncio.create_task(drain_pcm())

            try:
                while True:
                    item = await pcm_queue.get()
                    if item is None:
                        break
                    yield TTSAudioRawFrame(
                        audio=item,
                        sample_rate=self._sample_rate,
                        num_channels=1,
                    )
            finally:
                # Ensure both tasks finish; ffmpeg drains naturally on stdin close
                try:
                    await asyncio.wait_for(
                        asyncio.gather(feed_task, drain_task, return_exceptions=True),
                        timeout=10.0,
                    )
                except asyncio.TimeoutError:
                    feed_task.cancel()
                    drain_task.cancel()
                if proc.returncode is None:
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.wait()

            t_done = time.monotonic()
            ms = lambda a, b: (b - a) * 1000  # noqa: E731
            logger.info(
                "[TIMING] tts text=%r ttfb_mp3=%.0fms ttfb_pcm=%.0fms total=%.0fms %dB->%dB",
                text[:40],
                ms(t0, t_first_mp3 or t_done),
                ms(t0, t_first_pcm or t_done),
                ms(t0, t_done),
                mp3_bytes_total,
                pcm_bytes_total,
            )

            yield TTSStoppedFrame()

    _edge_tts_cls = EdgeTTSService
    return EdgeTTSService


def create_edge_tts_service(
    *,
    voice: str = "uk-UA-PolinaNeural",
    sample_rate: int = 24000,
    cache: "TTSCache | None" = None,
):
    cls = _build_edge_tts_class()
    return cls(voice=voice, sample_rate=sample_rate, cache=cache)
