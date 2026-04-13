"""Tests for src/speaker_processor.py — race-safe per-turn slot handoff.

All tests use pytest-asyncio so _TurnSlot.event is created inside a running
loop (asyncio.Event lazily binds on first use in py3.10+; constructing slots
in non-async fixtures would raise at .set() time).

speaker_id.embed is monkeypatched at the module boundary so the real
speechbrain/torch stack is never touched.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from src.config import Settings
from src.speaker_gallery import SpeakerGallery
from src.speaker_processor import (
    _SLOT_RETAIN,
    _TurnSlot,
    _build_processor_classes,
    create_speaker_processors,
)


def _settings() -> Settings:
    s = Settings()
    s.speaker_id_enabled = True
    return s


@pytest.fixture
def fake_frames():
    """Return the pipecat frame classes used by the tagger tests."""
    from pipecat.frames.frames import (
        AudioRawFrame,
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        InterimTranscriptionFrame,
        TranscriptionFrame,
        UserStartedSpeakingFrame,
        UserStoppedSpeakingFrame,
    )

    return {
        "audio": AudioRawFrame,
        "user_start": UserStartedSpeakingFrame,
        "user_stop": UserStoppedSpeakingFrame,
        "bot_start": BotStartedSpeakingFrame,
        "bot_stop": BotStoppedSpeakingFrame,
        "transcript": TranscriptionFrame,
        "interim": InterimTranscriptionFrame,
    }


def _owner_vector() -> np.ndarray:
    v = np.zeros(192, dtype=np.float32)
    v[0] = 1.0
    return v


def _stranger_vector() -> np.ndarray:
    v = np.zeros(192, dtype=np.float32)
    v[1] = 1.0
    return v


def _mk_gallery(tmp_path: Path) -> SpeakerGallery:
    g = SpeakerGallery(tmp_path / "speakers.json")
    g.enroll_owner(_owner_vector(), label="owner")
    return g


async def test_audio_buffer_captures_pcm_between_start_and_stop(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    import src.speaker_id as sid

    monkeypatch.setattr(sid, "embed", lambda pcm, sr, model: _owner_vector())
    gallery = _mk_gallery(tmp_path)
    buffer, tagger = create_speaker_processors(_settings(), gallery, model=MagicMock())

    start = fake_frames["user_start"]()
    stop = fake_frames["user_stop"]()
    audio1 = fake_frames["audio"](audio=b"\x01\x02" * 800, sample_rate=16000, num_channels=1)
    audio2 = fake_frames["audio"](audio=b"\x03\x04" * 800, sample_rate=16000, num_channels=1)

    await buffer.process_frame(start, None)
    await buffer.process_frame(audio1, None)
    await buffer.process_frame(audio2, None)
    await buffer.process_frame(stop, None)

    # Wait briefly for the fire-and-forget embed task to complete
    slot_id = buffer.latest_completed_turn_id()
    # It might not yet be set; give the task a moment
    for _ in range(20):
        if slot_id is not None:
            break
        await asyncio.sleep(0.01)
        slot_id = buffer.latest_completed_turn_id()
    assert slot_id == 1
    slot = buffer.get_slot(1)
    assert slot is not None
    expected_pcm = b"\x01\x02" * 800 + b"\x03\x04" * 800
    assert slot.pcm == expected_pcm
    assert slot.duration_ms == pytest.approx(100.0, abs=0.1)  # 1600 samples @16k
    await buffer.close()


async def test_sample_rate_assertion_raises(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    import src.speaker_id as sid

    monkeypatch.setattr(sid, "embed", lambda pcm, sr, model: _owner_vector())
    gallery = _mk_gallery(tmp_path)
    buffer, _ = create_speaker_processors(_settings(), gallery, model=MagicMock())

    start = fake_frames["user_start"]()
    await buffer.process_frame(start, None)
    bad_audio = fake_frames["audio"](audio=b"\x00\x00" * 100, sample_rate=48000, num_channels=1)
    with pytest.raises(RuntimeError, match="16000"):
        await buffer.process_frame(bad_audio, None)
    await buffer.close()


async def test_tagger_ignores_interim_transcriptions(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    import src.speaker_id as sid

    embed_calls = {"n": 0}

    def fake_embed(pcm, sr, model):
        embed_calls["n"] += 1
        return _owner_vector()

    monkeypatch.setattr(sid, "embed", fake_embed)
    gallery = _mk_gallery(tmp_path)
    buffer, tagger = create_speaker_processors(_settings(), gallery, model=MagicMock())

    interim = fake_frames["interim"](text="part", user_id="u", timestamp="t")
    await tagger.process_frame(interim, None)
    assert embed_calls["n"] == 0
    assert not hasattr(interim, "speaker_id") or interim.speaker_id is None
    await buffer.close()


async def test_tagger_ignores_transcription_with_finalized_false(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    import src.speaker_id as sid

    monkeypatch.setattr(sid, "embed", lambda pcm, sr, model: _owner_vector())
    gallery = _mk_gallery(tmp_path)
    buffer, tagger = create_speaker_processors(_settings(), gallery, model=MagicMock())

    frame = fake_frames["transcript"](text="partial", user_id="u", timestamp="t")
    frame.finalized = False
    await tagger.process_frame(frame, None)
    # No slot lookup → no speaker_id attribute attached
    assert getattr(frame, "speaker_id", "sentinel") == "sentinel"
    await buffer.close()


async def test_tagger_skips_during_bot_speaking(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    import src.speaker_id as sid

    monkeypatch.setattr(sid, "embed", lambda pcm, sr, model: _owner_vector())
    gallery = _mk_gallery(tmp_path)
    buffer, tagger = create_speaker_processors(_settings(), gallery, model=MagicMock())

    await tagger.process_frame(fake_frames["bot_start"](), None)
    frame = fake_frames["transcript"](text="hello", user_id="u", timestamp="t")
    frame.finalized = True
    await tagger.process_frame(frame, None)
    assert frame.speaker_id is None
    assert frame.speaker_confidence == -1.0
    await buffer.close()


async def test_tagger_fail_closed_on_slot_timeout(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    import src.speaker_id as sid

    # embed never completes — slot.event is never set
    async def never_embed(*args, **kwargs):
        await asyncio.sleep(10)

    # Replace the embed call via a long-running fake
    monkeypatch.setattr(sid, "embed", lambda pcm, sr, model: _owner_vector())
    gallery = _mk_gallery(tmp_path)
    buffer, tagger = create_speaker_processors(_settings(), gallery, model=MagicMock())

    # Manually install a slot whose event is never set
    stuck = _TurnSlot(turn_id=42)
    stuck.pcm = b"\x00\x00" * 8000  # 500ms worth
    stuck.duration_ms = 500.0
    buffer._slots[42] = stuck
    buffer._latest_completed_turn_id = 42

    frame = fake_frames["transcript"](text="hello", user_id="u", timestamp="t")
    frame.finalized = True
    import time as _t

    t0 = _t.monotonic()
    await tagger.process_frame(frame, None)
    elapsed = _t.monotonic() - t0
    assert elapsed < 0.3, f"tagger waited {elapsed:.2f}s — should fail closed within 200ms + slack"
    assert frame.speaker_id is None
    assert frame.speaker_confidence == 0.0
    await buffer.close()


async def test_tagger_matches_owner_via_mocked_embed(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    import src.speaker_id as sid

    monkeypatch.setattr(sid, "embed", lambda pcm, sr, model: _owner_vector())
    gallery = _mk_gallery(tmp_path)
    buffer, tagger = create_speaker_processors(_settings(), gallery, model=MagicMock())

    start = fake_frames["user_start"]()
    stop = fake_frames["user_stop"]()
    audio = fake_frames["audio"](audio=b"\x10\x20" * 8000, sample_rate=16000, num_channels=1)

    await buffer.process_frame(start, None)
    await buffer.process_frame(audio, None)
    await buffer.process_frame(stop, None)

    # Wait for embed
    for _ in range(20):
        if buffer.latest_completed_turn_id() is not None:
            break
        await asyncio.sleep(0.01)

    frame = fake_frames["transcript"](text="hello", user_id="u", timestamp="t")
    frame.finalized = True
    await tagger.process_frame(frame, None)
    assert frame.speaker_id == "owner"
    assert frame.speaker_confidence > 0.99
    assert frame.speaker_label == "owner"
    assert frame.speaker_inherited is False
    await buffer.close()


async def test_tagger_stranger_returns_none(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    import src.speaker_id as sid

    monkeypatch.setattr(sid, "embed", lambda pcm, sr, model: _stranger_vector())
    gallery = _mk_gallery(tmp_path)
    buffer, tagger = create_speaker_processors(_settings(), gallery, model=MagicMock())

    start = fake_frames["user_start"]()
    stop = fake_frames["user_stop"]()
    audio = fake_frames["audio"](audio=b"\x10\x20" * 8000, sample_rate=16000, num_channels=1)

    await buffer.process_frame(start, None)
    await buffer.process_frame(audio, None)
    await buffer.process_frame(stop, None)

    for _ in range(20):
        if buffer.latest_completed_turn_id() is not None:
            break
        await asyncio.sleep(0.01)

    frame = fake_frames["transcript"](text="hello", user_id="u", timestamp="t")
    frame.finalized = True
    await tagger.process_frame(frame, None)
    assert frame.speaker_id is None
    # stranger vector is orthogonal to owner → cosine ~0 (below 0.75)
    assert frame.speaker_confidence < 0.75
    await buffer.close()


async def test_short_turn_inherits_prev_label(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    import src.speaker_id as sid

    monkeypatch.setattr(sid, "embed", lambda pcm, sr, model: _owner_vector())
    gallery = _mk_gallery(tmp_path)
    buffer, tagger = create_speaker_processors(_settings(), gallery, model=MagicMock())

    # First a long turn to establish prev_id='owner'
    await buffer.process_frame(fake_frames["user_start"](), None)
    long_audio = fake_frames["audio"](audio=b"\x10\x20" * 8000, sample_rate=16000, num_channels=1)
    await buffer.process_frame(long_audio, None)
    await buffer.process_frame(fake_frames["user_stop"](), None)
    for _ in range(20):
        if buffer.latest_completed_turn_id() is not None:
            break
        await asyncio.sleep(0.01)
    frame1 = fake_frames["transcript"](text="долго", user_id="u", timestamp="t")
    frame1.finalized = True
    await tagger.process_frame(frame1, None)
    assert frame1.speaker_id == "owner"

    # Second: a very short turn (< 400ms voiced)
    await buffer.process_frame(fake_frames["user_start"](), None)
    short_audio = fake_frames["audio"](audio=b"\x00\x00" * 800, sample_rate=16000, num_channels=1)  # 50ms
    await buffer.process_frame(short_audio, None)
    await buffer.process_frame(fake_frames["user_stop"](), None)
    for _ in range(20):
        if buffer.latest_completed_turn_id() == 2:
            break
        await asyncio.sleep(0.01)

    frame2 = fake_frames["transcript"](text="так", user_id="u", timestamp="t")
    frame2.finalized = True
    await tagger.process_frame(frame2, None)
    assert frame2.speaker_id == "owner"  # inherited
    assert frame2.speaker_inherited is True
    assert frame2.speaker_confidence == -1.0
    await buffer.close()


async def test_gc_cancels_in_flight_task(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    import src.speaker_id as sid

    # Slow embed so we can cancel it before completion
    def slow_embed(pcm, sr, model):
        import time as _t

        _t.sleep(2.0)
        return _owner_vector()

    monkeypatch.setattr(sid, "embed", slow_embed)
    gallery = _mk_gallery(tmp_path)
    buffer, _ = create_speaker_processors(_settings(), gallery, model=MagicMock())

    # Spam _SLOT_RETAIN + 2 turns so the oldest gets evicted
    for _ in range(_SLOT_RETAIN + 2):
        await buffer.process_frame(fake_frames["user_start"](), None)
        a = fake_frames["audio"](audio=b"\x00\x00" * 8000, sample_rate=16000, num_channels=1)
        await buffer.process_frame(a, None)
        await buffer.process_frame(fake_frames["user_stop"](), None)

    # Evicted slots (turn_id 1 and 2) — their tasks should be cancelled
    assert 1 not in buffer._slots
    assert 2 not in buffer._slots
    # Remaining slots match retain count
    assert len(buffer._slots) == _SLOT_RETAIN
    await buffer.close()


async def test_shutdown_cancels_pending_tasks(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    import src.speaker_id as sid

    def slow_embed(pcm, sr, model):
        import time as _t

        _t.sleep(2.0)
        return _owner_vector()

    monkeypatch.setattr(sid, "embed", slow_embed)
    gallery = _mk_gallery(tmp_path)
    buffer, _ = create_speaker_processors(_settings(), gallery, model=MagicMock())

    # Arm 3 turns, all with long-running embeds
    for _ in range(3):
        await buffer.process_frame(fake_frames["user_start"](), None)
        a = fake_frames["audio"](audio=b"\x00\x00" * 8000, sample_rate=16000, num_channels=1)
        await buffer.process_frame(a, None)
        await buffer.process_frame(fake_frames["user_stop"](), None)

    pending_tasks = [s.task for s in buffer._slots.values() if s.task is not None]
    assert len(pending_tasks) == 3
    assert all(not t.done() for t in pending_tasks)

    import time as _t

    t0 = _t.monotonic()
    await buffer.close()
    elapsed = _t.monotonic() - t0
    assert elapsed < 1.2, f"close() took {elapsed:.1f}s — should cancel within 1s bound"
    assert all(t.done() for t in pending_tasks)


async def test_build_processor_classes_idempotent() -> None:
    """_build_processor_classes caches the pipecat subclass definitions."""
    c1 = _build_processor_classes()
    c2 = _build_processor_classes()
    assert c1[0] is c2[0]
    assert c1[1] is c2[1]
