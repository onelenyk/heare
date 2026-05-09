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
    from src.voice.tts.cache import TTSCache

logger = logging.getLogger("heare.tts")


def _apply_fade_out(pcm: bytes, sample_rate: int, fade_ms: int = 50) -> bytes:
    """Apply a linear-ramp fade-out to the last ``fade_ms`` of PCM s16le mono.

    CCS-05b: prevents an audible click when the cancel path drops a
    currently-speaking frame. PCM is signed 16-bit little-endian mono;
    the last ``fade_ms`` of samples (or the entire frame if shorter) are
    multiplied by a linear 1.0 → 0.0 ramp. Pure-Python; no numpy
    dependency on the test path.

    Returns a new bytes object — input is not mutated.
    """
    if not pcm:
        return pcm
    fade_samples = max(1, int(fade_ms * sample_rate / 1000))
    total_samples = len(pcm) // 2
    if total_samples == 0:
        return pcm
    n = min(fade_samples, total_samples)
    head = pcm[: (total_samples - n) * 2]
    tail = bytearray(pcm[(total_samples - n) * 2:])
    # Linear ramp 1.0 → 0.0 across n samples; write back as little-endian s16.
    for i in range(n):
        idx = i * 2
        # Decode signed 16-bit little-endian.
        lo = tail[idx]
        hi = tail[idx + 1]
        sample = lo | (hi << 8)
        if sample >= 0x8000:
            sample -= 0x10000
        # Multiplier 1 → 0 across the n samples.
        gain = 1.0 - (i / max(1, n - 1)) if n > 1 else 0.0
        scaled = int(sample * gain)
        # Re-encode (clamp defensively, though scaling can only reduce magnitude).
        if scaled < -0x8000:
            scaled = -0x8000
        elif scaled > 0x7FFF:
            scaled = 0x7FFF
        if scaled < 0:
            scaled += 0x10000
        tail[idx] = scaled & 0xFF
        tail[idx + 1] = (scaled >> 8) & 0xFF
    return bytes(head) + bytes(tail)

_edge_tts_cls = None


async def synthesize_to_pcm(text: str, voice: str, sample_rate: int) -> bytes:
    """One-shot synth: edge-tts → MP3 → ffmpeg → PCM bytes.

    Used by TTSCache.warmup at startup. Not on the hot path — the streaming
    pipeline in run_tts is what production calls use.
    """
    import edge_tts

    text = (text or "").strip()
    if not text or not any(c.isalnum() for c in text):
        return b""

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
            # CCS-05b: cancel signalling. ``_cancel_event`` is set by
            # ``cancel_pending`` so the in-flight ``run_tts`` generator
            # short-circuits at its next iteration. ``_pending_pcm`` is the
            # most recently fetched (cached) PCM buffer — when a cancel
            # arrives mid-emit, we apply a 50ms fade-out to the chunk
            # currently being yielded and drop everything after.
            self._cancel_event = asyncio.Event()
            self._cancel_dropped: int = 0
            self._last_chunk: bytes = b""
            # Optional reference to the live ffmpeg process so cancel can
            # tear it down without waiting for natural EOF.
            self._live_proc: Any = None

        def set_voice(self, voice: str) -> None:
            self._voice = voice
            self._settings.voice = voice

        def cancel_pending(self) -> int:
            """CCS-05b: drop queued/upcoming TTS frames with a 50ms fade-out.

            Synchronous: sets the cancel event so any in-flight
            ``run_tts`` generator short-circuits at its next iteration,
            optionally tears down the live ffmpeg process, and returns the
            number of frames dropped (best-effort — pre-frame counter
            captures the residual of the chunk-emit loop). The currently-
            speaking frame is faded out via ``_apply_fade_out`` before the
            generator yields it.
            """
            self._cancel_event.set()
            # Best-effort teardown of the live ffmpeg subprocess so no
            # further PCM bytes are produced.
            proc = self._live_proc
            if proc is not None:
                try:
                    if getattr(proc, "returncode", None) is None:
                        proc.kill()
                except Exception:  # noqa: BLE001
                    logger.debug("EdgeTTSService cancel: proc.kill failed", exc_info=True)
            dropped = self._cancel_dropped
            self._cancel_dropped = 0
            return dropped

        async def run_tts(
            self, text: str, context_id: str | None = None
        ) -> AsyncGenerator[Frame, None]:
            text = (text or "").strip()
            if not text or not any(c.isalnum() for c in text):
                # edge-tts rejects punctuation-only text with NoAudioReceived.
                yield TTSStoppedFrame()
                return

            # New utterance — reset cancel state so a stale cancel from a
            # prior run can't poison this generator.
            self._cancel_event.clear()
            self._cancel_dropped = 0

            chunk_size = self._sample_rate * 2 // 10  # ~100ms of 16-bit mono

            # Cache fast path: pre-rendered PCM for fixed phrases.
            if self._cache is not None:
                cached = self._cache.get(text)
                if cached:
                    t_cache = time.monotonic()
                    chunks = [
                        cached[i : i + chunk_size]
                        for i in range(0, len(cached), chunk_size)
                    ]
                    self._cancel_dropped = len(chunks)
                    for idx, chunk in enumerate(chunks):
                        if self._cancel_event.is_set():
                            # Apply 50ms fade-out to the chunk we are
                            # about to yield, then drop the rest.
                            faded = _apply_fade_out(chunk, self._sample_rate, fade_ms=50)
                            self._last_chunk = faded
                            yield TTSAudioRawFrame(
                                audio=faded,
                                sample_rate=self._sample_rate,
                                num_channels=1,
                            )
                            self._cancel_dropped = len(chunks) - (idx + 1)
                            yield TTSStoppedFrame()
                            return
                        self._last_chunk = chunk
                        yield TTSAudioRawFrame(
                            audio=chunk,
                            sample_rate=self._sample_rate,
                            num_channels=1,
                        )
                        self._cancel_dropped = len(chunks) - (idx + 1)
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
                self._live_proc = proc
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
                    if self._cancel_event.is_set():
                        # Apply 50ms fade-out to the chunk we're about to
                        # yield, then drop everything queued after.
                        faded = _apply_fade_out(item, self._sample_rate, fade_ms=50)
                        self._last_chunk = faded
                        yield TTSAudioRawFrame(
                            audio=faded,
                            sample_rate=self._sample_rate,
                            num_channels=1,
                        )
                        break
                    self._last_chunk = item
                    yield TTSAudioRawFrame(
                        audio=item,
                        sample_rate=self._sample_rate,
                        num_channels=1,
                    )
            finally:
                self._live_proc = None
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
