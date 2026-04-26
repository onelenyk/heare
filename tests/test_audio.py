"""Audio path tests — real transcoding, frame format, chunk sizes, end-to-end flow.

These tests exercise the actual audio pipeline: MP3→PCM transcoding via ffmpeg,
audio frame format verification, chunk sizing, and full frame flow through
DeciderProcessor + EdgeTTSService.

Tests requiring ffmpeg are skipped if it's not installed.
"""
from __future__ import annotations

import shutil
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

HAS_FFMPEG = shutil.which("ffmpeg") is not None

pipecat = pytest.importorskip("pipecat.frames.frames")
pytest.importorskip("edge_tts")

from pipecat.frames.frames import (  # noqa: E402
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    TranscriptionFrame,
)

import src.tts_edge as tts_mod  # noqa: E402
from src.config import Mode, Settings  # noqa: E402
from src.context import ContextBuilder  # noqa: E402
from src.decider import create_decider_processor  # noqa: E402
from src.storage import TranscriptStore  # noqa: E402
from src.tts_edge import _mp3_to_pcm_s16le, create_edge_tts_service  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_mp3_silence(duration_s: float = 0.1, sample_rate: int = 24000) -> bytes:
    """Generate a real MP3 file containing silence via ffmpeg."""
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
    assert proc.returncode == 0, f"ffmpeg failed: {proc.stderr.decode()[:200]}"
    assert len(proc.stdout) > 0
    return proc.stdout


def _generate_mp3_tone(freq: int = 440, duration_s: float = 0.1, sample_rate: int = 24000) -> bytes:
    """Generate a real MP3 with a sine tone via ffmpeg."""
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"sine=frequency={freq}:sample_rate={sample_rate}:duration={duration_s}",
            "-acodec", "libmp3lame",
            "-f", "mp3", "pipe:1",
        ],
        capture_output=True,
    )
    assert proc.returncode == 0
    return proc.stdout


class FakeClaudeCLI:
    def __init__(self, decisions: list[dict[str, Any]]) -> None:
        self._decisions = decisions
        self.call_action = AsyncMock(return_value={"summary": "ok, done"})

    async def call_decider(self, prompt: str) -> dict[str, Any]:
        return self._decisions.pop(0)


class _FakeCommunicateWithRealMP3:
    """Feeds a real MP3 (silence) into the TTS pipeline so ffmpeg gets real data."""

    def __init__(self, mp3_data: bytes):
        self._mp3_data = mp3_data

    def __call__(self, text: str, voice: str):
        self._text = text
        return self

    async def stream(self):
        chunk_size = len(self._mp3_data) // 2 or len(self._mp3_data)
        for i in range(0, len(self._mp3_data), chunk_size):
            yield {"type": "audio", "data": self._mp3_data[i:i + chunk_size]}


def _make_frame(text: str):
    try:
        return TranscriptionFrame(text=text, user_id="u", timestamp="t")
    except TypeError:
        return TranscriptionFrame(user_id="u", timestamp="t", text=text)


# ---------------------------------------------------------------------------
# 1. Real MP3 → PCM transcoding
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_transcode_silence_produces_valid_pcm():
    """_mp3_to_pcm_s16le with real silent MP3 → valid PCM bytes."""
    mp3 = _generate_mp3_silence(duration_s=0.1, sample_rate=24000)
    pcm = await _mp3_to_pcm_s16le(mp3, sample_rate=24000)

    assert len(pcm) > 0
    # PCM must be even length (16-bit = 2 bytes per sample)
    assert len(pcm) % 2 == 0
    # ~0.1s @ 24kHz mono s16le ≈ 4800 bytes (MP3 encoding adds padding)
    assert len(pcm) >= 2400  # at least 0.05s worth


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_transcode_tone_has_nonzero_samples():
    """A 440Hz tone should produce PCM with non-zero sample values."""
    mp3 = _generate_mp3_tone(freq=440, duration_s=0.1, sample_rate=24000)
    pcm = await _mp3_to_pcm_s16le(mp3, sample_rate=24000)

    # Decode s16le samples and check some are non-zero
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    max_abs = max(abs(s) for s in samples)
    assert max_abs > 100, f"Expected audible tone but max sample amplitude is {max_abs}"


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_transcode_respects_sample_rate():
    """Transcoding at 16kHz should produce fewer bytes than 24kHz for same duration."""
    mp3 = _generate_mp3_silence(duration_s=0.2, sample_rate=24000)
    pcm_24k = await _mp3_to_pcm_s16le(mp3, sample_rate=24000)
    pcm_16k = await _mp3_to_pcm_s16le(mp3, sample_rate=16000)

    # 16kHz should have roughly 2/3 the bytes of 24kHz
    assert len(pcm_16k) < len(pcm_24k)
    ratio = len(pcm_16k) / len(pcm_24k)
    assert 0.5 < ratio < 0.85, f"Sample rate ratio {ratio} outside expected range"


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_transcode_invalid_mp3_raises():
    """Feeding garbage to ffmpeg should raise RuntimeError."""
    with pytest.raises(RuntimeError, match="ffmpeg failed"):
        await _mp3_to_pcm_s16le(b"this is not an mp3", sample_rate=24000)


# ---------------------------------------------------------------------------
# 2. TTS audio frame format and chunking
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_tts_audio_frames_have_correct_format():
    """AudioRawFrames from TTS must have sample_rate=24000, num_channels=1."""
    tts_mod._edge_tts_cls = None
    service = create_edge_tts_service(voice="uk-UA-PolinaNeural", sample_rate=24000)
    mp3 = _generate_mp3_silence(duration_s=0.1)
    fake_comm = _FakeCommunicateWithRealMP3(mp3)

    with patch("edge_tts.Communicate", fake_comm):
        frames = [f async for f in service.run_tts("тест")]

    audio_frames = [f for f in frames if hasattr(f, "audio")]
    assert len(audio_frames) >= 1

    for af in audio_frames:
        assert af.sample_rate == 24000
        assert af.num_channels == 1


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_tts_chunk_size_approximately_100ms():
    """Each audio chunk should be ~100ms = sample_rate * 2 / 10 bytes."""
    tts_mod._edge_tts_cls = None
    sample_rate = 24000
    service = create_edge_tts_service(sample_rate=sample_rate)
    # Generate enough audio to get multiple chunks
    mp3 = _generate_mp3_tone(duration_s=0.5, sample_rate=sample_rate)
    fake_comm = _FakeCommunicateWithRealMP3(mp3)

    with patch("edge_tts.Communicate", fake_comm):
        frames = [f async for f in service.run_tts("довший текст")]

    audio_frames = [f for f in frames if hasattr(f, "audio")]
    expected_chunk = sample_rate * 2 // 10  # 4800 bytes for 24kHz

    # All chunks except the last should be exactly expected_chunk bytes
    for af in audio_frames[:-1]:
        assert len(af.audio) == expected_chunk, (
            f"Chunk size {len(af.audio)} != expected {expected_chunk}"
        )
    # Last chunk can be smaller (remainder)
    if audio_frames:
        assert len(audio_frames[-1].audio) <= expected_chunk


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_tts_total_pcm_matches_transcode_output():
    """Total bytes across all audio frames must equal the raw PCM from ffmpeg."""
    tts_mod._edge_tts_cls = None
    mp3 = _generate_mp3_tone(duration_s=0.3, sample_rate=24000)
    fake_comm = _FakeCommunicateWithRealMP3(mp3)
    expected_pcm = await _mp3_to_pcm_s16le(mp3, 24000)

    service = create_edge_tts_service(sample_rate=24000)
    with patch("edge_tts.Communicate", fake_comm):
        frames = [f async for f in service.run_tts("привіт")]

    audio_frames = [f for f in frames if hasattr(f, "audio")]
    total = b"".join(af.audio for af in audio_frames)
    assert total == expected_pcm


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_tts_stopped_frame_always_last():
    """The last frame from run_tts must always be TTSStoppedFrame."""
    tts_mod._edge_tts_cls = None
    mp3 = _generate_mp3_silence(duration_s=0.1)
    fake_comm = _FakeCommunicateWithRealMP3(mp3)

    service = create_edge_tts_service()
    with patch("edge_tts.Communicate", fake_comm):
        frames = [f async for f in service.run_tts("тест")]

    assert len(frames) >= 2  # at least 1 audio + 1 stopped
    assert type(frames[-1]).__name__ == "TTSStoppedFrame"
    # All non-last frames should be audio
    for f in frames[:-1]:
        assert hasattr(f, "audio"), f"Expected audio frame, got {type(f).__name__}"


# ---------------------------------------------------------------------------
# 3. End-to-end: transcript in → decider → TTS → audio frames out
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
@pytest.mark.skip(
    reason="pending DeciderProcessor → GeneratorProcessor migration (PRD WU US-WU-05)"
)
async def test_end_to_end_speak_produces_audio_frames():
    """Full flow: transcript → decider says speak → TTS generates real audio frames."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "heare.db"
        store = TranscriptStore(db)
        await store.init()
        settings = Settings()
        settings.mode = Mode.AMBIENT
        settings.mode_file = Path(tmp) / "mode"  # prevent reading real file
        ctx = ContextBuilder(store, settings)

        cli = FakeClaudeCLI([
            {"type": "speak", "confidence": 0.95, "reply": "Привіт, я тут!", "reason": "greeted"}
        ])
        decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")

        # Collect all frames pushed by decider
        pushed_frames: list = []

        async def capture_push(frame, direction=None):
            pushed_frames.append(frame)

        decider.push_frame = capture_push  # type: ignore[assignment]

        # Process a transcript (wake-word prefixed so LAT-B1 doesn't filter it)
        await decider._handle_listening("Гава, привіт")

        # Decider should push a TTSSpeakFrame
        assert len(pushed_frames) == 1
        speak_frame = pushed_frames[0]
        assert type(speak_frame).__name__ == "TTSSpeakFrame"
        assert "Привіт" in getattr(speak_frame, "text", "")

        # Now feed that TTSSpeakFrame text through real TTS
        tts_mod._edge_tts_cls = None
        tts = create_edge_tts_service(sample_rate=24000)
        mp3 = _generate_mp3_silence(duration_s=0.1)
        fake_comm = _FakeCommunicateWithRealMP3(mp3)

        with patch("edge_tts.Communicate", fake_comm):
            tts_frames = [f async for f in tts.run_tts(speak_frame.text)]

        audio_frames = [f for f in tts_frames if hasattr(f, "audio")]
        assert len(audio_frames) >= 1
        # Verify real PCM audio
        total_audio = b"".join(af.audio for af in audio_frames)
        assert len(total_audio) > 0
        assert len(total_audio) % 2 == 0  # valid s16le

        await store.close()


# ---------------------------------------------------------------------------
# 4. Echo cancellation — audio suppression during bot speech
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="pending DeciderProcessor → GeneratorProcessor migration (PRD WU US-WU-05)"
)
async def test_echo_cancellation_full_cycle():
    """Transcripts during bot speech and during cooldown are dropped; after cooldown, processing resumes."""
    import asyncio as _asyncio

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "heare.db"
        store = TranscriptStore(db)
        await store.init()
        settings = Settings()
        settings.mode = Mode.AMBIENT
        settings.mode_file = Path(tmp) / "mode"
        settings.bot_speaking_cooldown_seconds = 0.05
        ctx = ContextBuilder(store, settings)

        decisions = [
            {"type": "speak", "reply": "real question", "confidence": 0.9, "reason": "test"},
        ]
        cli = FakeClaudeCLI(decisions)
        decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")

        pushed: list = []

        async def capture(frame, direction=None):
            pushed.append(frame)

        decider.push_frame = capture  # type: ignore[assignment]

        # 1. Bot starts speaking
        await decider.process_frame(BotStartedSpeakingFrame(), None)
        assert decider._bot_speaking is True

        # 2. Transcript arrives while bot is speaking — should be IGNORED
        frame_during = _make_frame("background noise")
        await decider.process_frame(frame_during, None)
        assert len(cli._decisions) == 1  # decider not called

        # 3. Bot stops speaking — cooldown is now active
        await decider.process_frame(BotStoppedSpeakingFrame(), None)
        assert decider._bot_speaking is True  # still True due to cooldown

        # 4. Late-arriving STT echo during cooldown — must be IGNORED
        frame_late_echo = _make_frame("echo of bot voice")
        await decider.process_frame(frame_late_echo, None)
        assert len(cli._decisions) == 1  # still not called

        # 5. After cooldown elapses
        await _asyncio.sleep(0.1)
        assert decider._bot_speaking is False

        # 6. Real transcript — should be processed (wake-word bypasses B1 filter)
        frame_after = _make_frame("Гава, що там")
        await decider.process_frame(frame_after, None)
        assert len(cli._decisions) == 0  # decision consumed

        await store.close()


@pytest.mark.skip(
    reason="pending DeciderProcessor → GeneratorProcessor migration (PRD WU US-WU-05)"
)
async def test_echo_cancellation_preserves_bot_frames():
    """BotStarted/StoppedSpeakingFrames are always passed through to output."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "heare.db"
        store = TranscriptStore(db)
        await store.init()
        settings = Settings()
        settings.mode = Mode.AMBIENT
        settings.mode_file = Path(tmp) / "mode"
        ctx = ContextBuilder(store, settings)

        cli = FakeClaudeCLI([])
        decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")

        pushed: list = []

        async def capture(frame, direction=None):
            pushed.append(frame)

        decider.push_frame = capture  # type: ignore[assignment]

        await decider.process_frame(BotStartedSpeakingFrame(), None)
        await decider.process_frame(BotStoppedSpeakingFrame(), None)

        assert len(pushed) == 2
        assert isinstance(pushed[0], BotStartedSpeakingFrame)
        assert isinstance(pushed[1], BotStoppedSpeakingFrame)

        await store.close()


# ---------------------------------------------------------------------------
# 5. Audio format edge cases
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_transcode_very_short_audio():
    """Very short audio (10ms) should still produce valid PCM."""
    mp3 = _generate_mp3_silence(duration_s=0.01, sample_rate=24000)
    pcm = await _mp3_to_pcm_s16le(mp3, sample_rate=24000)
    assert len(pcm) > 0
    assert len(pcm) % 2 == 0


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_transcode_at_different_sample_rates():
    """Transcode produces valid PCM at 8kHz, 16kHz, 24kHz, 48kHz."""
    mp3 = _generate_mp3_tone(duration_s=0.1, sample_rate=24000)
    for rate in (8000, 16000, 24000, 48000):
        pcm = await _mp3_to_pcm_s16le(mp3, sample_rate=rate)
        assert len(pcm) > 0
        assert len(pcm) % 2 == 0
        # Verify sample count is roughly proportional to rate
        n_samples = len(pcm) // 2
        expected_min = int(rate * 0.05)  # at least 50ms worth
        assert n_samples >= expected_min, f"At {rate}Hz: {n_samples} samples < {expected_min}"


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
async def test_pcm_samples_are_valid_s16le():
    """All PCM samples must be within s16le range [-32768, 32767]."""
    mp3 = _generate_mp3_tone(freq=1000, duration_s=0.1, sample_rate=24000)
    pcm = await _mp3_to_pcm_s16le(mp3, sample_rate=24000)
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    for s in samples:
        assert -32768 <= s <= 32767
