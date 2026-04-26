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


# -------- CCS-05b: cancel_pending + 50ms fade-out --------


def test_apply_fade_out_ramps_amplitude_to_zero() -> None:
    """Helper: 50ms fade-out is monotonically decreasing across the last samples."""
    from src.tts_edge import _apply_fade_out

    sample_rate = 24000
    fade_ms = 50
    fade_samples = int(fade_ms * sample_rate / 1000)
    # Build a constant-amplitude PCM frame (s16le mono): all samples = 10000.
    n_total = fade_samples * 2  # 100ms of audio so head + tail visible
    pcm = bytearray()
    for _ in range(n_total):
        pcm += (10000).to_bytes(2, "little", signed=True)

    faded = _apply_fade_out(bytes(pcm), sample_rate, fade_ms=fade_ms)

    # Decode the last fade_samples samples and verify they ramp toward 0.
    last_chunk = faded[-fade_samples * 2:]
    samples = []
    for i in range(0, len(last_chunk), 2):
        s = int.from_bytes(last_chunk[i:i + 2], "little", signed=True)
        samples.append(s)
    # First sample of the tail should be near full amplitude;
    # last sample should be near zero.
    assert abs(samples[0]) >= 9000, f"start of fade should still be loud, got {samples[0]}"
    assert abs(samples[-1]) <= 100, f"end of fade should be near 0, got {samples[-1]}"
    # Strictly non-increasing magnitude across the fade region.
    for prev, cur in zip(samples, samples[1:]):
        assert abs(cur) <= abs(prev) + 1, (
            f"fade should be monotonically decreasing in magnitude (prev={prev} cur={cur})"
        )


def test_apply_fade_out_no_op_for_empty_pcm() -> None:
    from src.tts_edge import _apply_fade_out

    assert _apply_fade_out(b"", 24000) == b""


async def test_run_tts_cancel_pending_drops_remaining_cached_frames() -> None:
    """AC#6: cancel_pending stops emitting queued cached frames, with a
    fade-out applied to the chunk being yielded at the time of cancel."""
    tts_mod._edge_tts_cls = None
    sample_rate = 24000

    # Pre-populate the cache with 1 second of silence (10 chunks).
    cached_pcm = b"\x00\x10" * (sample_rate)  # 1s of constant-amplitude samples
    assert len(cached_pcm) == sample_rate * 2

    class _Cache:
        def get(self, text):
            return cached_pcm

    service = create_edge_tts_service(sample_rate=sample_rate, cache=_Cache())

    yielded: list = []
    gen = service.run_tts("привіт")
    # Pull a couple of frames, then call cancel_pending mid-stream.
    first = await gen.__anext__()
    yielded.append(first)
    second = await gen.__anext__()
    yielded.append(second)
    dropped = service.cancel_pending()
    assert isinstance(dropped, int)
    # Remaining frames should be: one faded chunk + TTSStoppedFrame, then end.
    rest = []
    async for frame in gen:
        rest.append(frame)
    # At most one more audio frame (the faded chunk) + a TTSStoppedFrame.
    audio_after = [f for f in rest if hasattr(f, "audio")]
    stopped_after = [f for f in rest if type(f).__name__ == "TTSStoppedFrame"]
    assert len(audio_after) <= 1, (
        f"expected at most 1 faded audio frame after cancel, got {len(audio_after)}"
    )
    assert len(stopped_after) == 1, "must yield exactly one TTSStoppedFrame on cancel"
    # Total audio frames yielded must be < 10 (the full uncancelled count).
    total_audio = len([f for f in yielded if hasattr(f, "audio")]) + len(audio_after)
    assert total_audio < 10, (
        f"cancel should have dropped frames; got {total_audio} of 10"
    )


async def test_run_tts_cancel_pending_applies_fade_to_inflight_chunk() -> None:
    """AC#6: the chunk yielded at the moment of cancel has the last 50ms
    of samples ramped toward zero (no audible click)."""
    tts_mod._edge_tts_cls = None
    sample_rate = 24000

    # Build cached PCM: 5 chunks of 100ms each, all constant amplitude.
    chunk_size = sample_rate * 2 // 10
    full_amp = 10000
    one_chunk = b""
    for _ in range(chunk_size // 2):
        one_chunk += (full_amp).to_bytes(2, "little", signed=True)
    cached_pcm = one_chunk * 5

    class _Cache:
        def get(self, text):
            return cached_pcm

    service = create_edge_tts_service(sample_rate=sample_rate, cache=_Cache())

    gen = service.run_tts("test")
    # Drain first chunk to confirm baseline (un-faded).
    first = await gen.__anext__()
    assert hasattr(first, "audio")
    # Decode last sample of first chunk — should be at full amplitude.
    last_sample = int.from_bytes(first.audio[-2:], "little", signed=True)
    assert abs(last_sample) == full_amp, (
        f"baseline first chunk should not be faded, got tail={last_sample}"
    )

    service.cancel_pending()

    # Next yielded audio frame should be the faded chunk.
    faded = await gen.__anext__()
    assert hasattr(faded, "audio")
    # Last sample should be at or near zero; first sample of the chunk
    # should still be at full amplitude (fade only applies to last 50ms).
    last_pcm = faded.audio
    last_sample = int.from_bytes(last_pcm[-2:], "little", signed=True)
    first_sample = int.from_bytes(last_pcm[:2], "little", signed=True)
    assert abs(last_sample) < 200, (
        f"end of faded chunk should ramp to 0, got {last_sample}"
    )
    assert abs(first_sample) == full_amp, (
        f"start of cancel chunk should be full amplitude, got {first_sample}"
    )

    # Drain the rest (TTSStoppedFrame).
    async for _ in gen:
        pass


async def test_cancel_pending_is_idempotent_and_synchronous() -> None:
    """AC: cancel_pending returns int and is safe to call repeatedly."""
    tts_mod._edge_tts_cls = None
    service = create_edge_tts_service()
    # Call cancel_pending without an active run_tts — should not raise.
    n1 = service.cancel_pending()
    n2 = service.cancel_pending()
    assert isinstance(n1, int)
    assert isinstance(n2, int)
