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
    _AccumBuffer,
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


async def test_prev_id_cleared_on_non_owner_turn(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    """SPK2-B1: a non-owner turn (no gallery match) must clear _prev_id
    so a subsequent short turn does NOT inherit the previous owner label.
    """
    import src.speaker_id as sid

    state = {"vec": _owner_vector()}
    monkeypatch.setattr(sid, "embed", lambda pcm, sr, model: state["vec"].copy())
    gallery = _mk_gallery(tmp_path)
    buffer, tagger = create_speaker_processors(_settings(), gallery, model=MagicMock())

    async def _run_turn(audio_bytes: bytes) -> None:
        prev_completed = buffer.latest_completed_turn_id()
        await buffer.process_frame(fake_frames["user_start"](), None)
        await buffer.process_frame(
            fake_frames["audio"](audio=audio_bytes, sample_rate=16000, num_channels=1),
            None,
        )
        await buffer.process_frame(fake_frames["user_stop"](), None)
        for _ in range(50):
            cur = buffer.latest_completed_turn_id()
            if cur is not None and cur != prev_completed:
                break
            await asyncio.sleep(0.01)

    # Turn 1: owner, long enough to match
    state["vec"] = _owner_vector()
    await _run_turn(b"\x10\x20" * 8000)  # 1000ms @ 16k
    f1 = fake_frames["transcript"](text="long owner", user_id="u", timestamp="t")
    f1.finalized = True
    await tagger.process_frame(f1, None)
    assert f1.speaker_id == "owner"
    assert tagger._prev_id == "owner"

    # Turn 2: stranger, long enough to run identify. Gallery only has owner;
    # orthogonal stranger vector → gallery.identify returns (None, small).
    state["vec"] = _stranger_vector()
    await _run_turn(b"\x30\x40" * 8000)
    f2 = fake_frames["transcript"](text="long stranger", user_id="u", timestamp="t")
    f2.finalized = True
    await tagger.process_frame(f2, None)
    assert f2.speaker_id is None
    # The leak fix: prev identity must be CLEARED now
    assert tagger._prev_id is None

    # Turn 3: a short turn — must NOT inherit "owner" via latent _prev_id
    state["vec"] = _owner_vector()  # wouldn't matter; short-turn skips embed
    await _run_turn(b"\x00\x00" * 800)  # 50ms
    f3 = fake_frames["transcript"](text="так", user_id="u", timestamp="t")
    f3.finalized = True
    await tagger.process_frame(f3, None)
    assert f3.speaker_id is None
    assert f3.speaker_inherited is False
    await buffer.close()


async def test_sticky_window_expires_after_timeout(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    """SPK2-B1: once speaker_id_sticky_seconds elapses, short turns no
    longer inherit the previous label and fall through as unknown.

    Rather than patching global time.monotonic (which deadlocks asyncio
    internals), we artificially age self._prev_at by subtracting the
    sticky window + slack directly on the tagger instance.
    """
    import src.speaker_id as sid

    monkeypatch.setattr(sid, "embed", lambda pcm, sr, model: _owner_vector())
    gallery = _mk_gallery(tmp_path)
    settings = _settings()
    settings.speaker_id_sticky_seconds = 5.0
    buffer, tagger = create_speaker_processors(settings, gallery, model=MagicMock())

    # Turn 1: owner, long, matches — establishes prev_id + _prev_at
    await buffer.process_frame(fake_frames["user_start"](), None)
    await buffer.process_frame(
        fake_frames["audio"](
            audio=b"\x10\x20" * 8000, sample_rate=16000, num_channels=1
        ),
        None,
    )
    await buffer.process_frame(fake_frames["user_stop"](), None)
    for _ in range(30):
        if buffer.latest_completed_turn_id() is not None:
            break
        await asyncio.sleep(0.01)
    f1 = fake_frames["transcript"](text="long owner", user_id="u", timestamp="t")
    f1.finalized = True
    await tagger.process_frame(f1, None)
    assert tagger._prev_id == "owner"
    assert tagger._prev_at > 0.0

    # Age the sticky window past expiry (5s + 1s slack)
    tagger._prev_at -= 6.0

    # Turn 2: short, 50ms — sticky window expired so must NOT inherit
    await buffer.process_frame(fake_frames["user_start"](), None)
    await buffer.process_frame(
        fake_frames["audio"](
            audio=b"\x00\x00" * 800, sample_rate=16000, num_channels=1
        ),
        None,
    )
    await buffer.process_frame(fake_frames["user_stop"](), None)
    for _ in range(30):
        if buffer.latest_completed_turn_id() == 2:
            break
        await asyncio.sleep(0.01)
    f2 = fake_frames["transcript"](text="так", user_id="u", timestamp="t")
    f2.finalized = True
    await tagger.process_frame(f2, None)
    assert f2.speaker_id is None
    assert f2.speaker_inherited is False
    await buffer.close()


async def test_short_turn_after_stranger_does_not_inherit_owner(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    """SPK2-B1: end-to-end through the tagger's short-turn path — a short
    'так' that arrives right after a non-matched long turn must fall
    through as None (not inherit the owner id from two turns ago).
    """
    import src.speaker_id as sid

    state = {"vec": _owner_vector()}
    monkeypatch.setattr(sid, "embed", lambda pcm, sr, model: state["vec"].copy())
    gallery = _mk_gallery(tmp_path)
    buffer, tagger = create_speaker_processors(_settings(), gallery, model=MagicMock())

    async def _run(audio: bytes) -> None:
        prev_completed = buffer.latest_completed_turn_id()
        await buffer.process_frame(fake_frames["user_start"](), None)
        await buffer.process_frame(
            fake_frames["audio"](audio=audio, sample_rate=16000, num_channels=1),
            None,
        )
        await buffer.process_frame(fake_frames["user_stop"](), None)
        for _ in range(50):
            cur = buffer.latest_completed_turn_id()
            if cur is not None and cur != prev_completed:
                break
            await asyncio.sleep(0.01)

    # 1. owner long → prev_id=owner
    state["vec"] = _owner_vector()
    await _run(b"\x10\x20" * 8000)
    f1 = fake_frames["transcript"](text="long owner", user_id="u", timestamp="t")
    f1.finalized = True
    await tagger.process_frame(f1, None)
    assert tagger._prev_id == "owner"

    # 2. stranger long → prev cleared
    state["vec"] = _stranger_vector()
    await _run(b"\x30\x40" * 8000)
    f2 = fake_frames["transcript"](text="stranger long", user_id="u", timestamp="t")
    f2.finalized = True
    await tagger.process_frame(f2, None)
    assert tagger._prev_id is None

    # 3. short "так" — must NOT inherit owner
    await _run(b"\x00\x00" * 800)
    f3 = fake_frames["transcript"](text="так", user_id="u", timestamp="t")
    f3.finalized = True
    await tagger.process_frame(f3, None)
    assert f3.speaker_id is None
    assert f3.speaker_inherited is False
    await buffer.close()


def test_accum_buffer_rolls_over_duration_limit() -> None:
    """SPK2-B2: _AccumBuffer caps total audio at 2x the target duration."""
    buf = _AccumBuffer(target_ms=3000, sample_rate=16000)
    # Each chunk = 500ms @ 16k int16 mono = 16000 bytes
    chunk = b"\x00\x01" * 8000
    for _ in range(20):  # 10_000ms worth of audio fed in
        buf.append(chunk)
    # Cap is 2 * 3000ms = 6000ms; buffer should hold no more than that
    assert buf.total_ms() <= 6000.0
    # And at least one target-window's worth remains
    assert buf.total_ms() >= 3000.0


async def test_accum_buffer_flushed_on_bot_speaking(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    """SPK2-B2: BotStartedSpeakingFrame flushes the rolling accumulator so
    TTS audio never contaminates the stored context.
    """
    import src.speaker_id as sid

    monkeypatch.setattr(sid, "embed", lambda pcm, sr, model: _owner_vector())
    gallery = _mk_gallery(tmp_path)
    buffer, _ = create_speaker_processors(_settings(), gallery, model=MagicMock())

    # Feed 1 second of audio through a user turn so the accumulator grows.
    await buffer.process_frame(fake_frames["user_start"](), None)
    audio = fake_frames["audio"](
        audio=b"\x10\x20" * 8000, sample_rate=16000, num_channels=1
    )
    await buffer.process_frame(audio, None)
    await buffer.process_frame(fake_frames["user_stop"](), None)
    assert buffer._accum.total_ms() > 0.0

    # Bot starts speaking → accumulator must flush
    await buffer.process_frame(fake_frames["bot_start"](), None)
    assert buffer._accum.total_ms() == 0.0
    await buffer.close()


async def test_short_turn_uses_accumulated_embedding(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    """SPK2-B2: when a sub-target-duration turn completes and the rolling
    buffer has reached target, the slot receives an accum_embedding AND
    the tagger uses it (not the single-turn embedding) for gallery.identify.
    """
    import src.speaker_id as sid

    call_log: list[int] = []

    def tracking_embed(pcm, sr, model):
        call_log.append(len(pcm))
        return _owner_vector()

    monkeypatch.setattr(sid, "embed", tracking_embed)
    gallery = _mk_gallery(tmp_path)
    settings = _settings()
    settings.speaker_id_accum_target_ms = 3000
    settings.speaker_id_min_duration_ms = 400
    buffer, tagger = create_speaker_processors(settings, gallery, model=MagicMock())

    async def _run_turn(audio_bytes: bytes) -> int:
        prev = buffer.latest_completed_turn_id()
        await buffer.process_frame(fake_frames["user_start"](), None)
        await buffer.process_frame(
            fake_frames["audio"](
                audio=audio_bytes, sample_rate=16000, num_channels=1
            ),
            None,
        )
        await buffer.process_frame(fake_frames["user_stop"](), None)
        for _ in range(50):
            cur = buffer.latest_completed_turn_id()
            if cur is not None and cur != prev:
                return cur
            await asyncio.sleep(0.01)
        raise AssertionError("slot never completed")

    # 3 back-to-back 1-second turns so the accumulator reaches 3000ms
    second_pcm = b"\x10\x20" * 16000  # 1000ms at 16k int16
    _ = await _run_turn(second_pcm)
    _ = await _run_turn(second_pcm)
    last_turn = await _run_turn(second_pcm)

    slot = buffer.get_slot(last_turn)
    assert slot is not None
    assert slot.duration_ms < settings.speaker_id_accum_target_ms
    assert buffer._accum.total_ms() >= settings.speaker_id_accum_target_ms
    assert slot.accum_embedding is not None

    # Tagger must use the accumulated embedding, not the single-turn one
    frame = fake_frames["transcript"](text="accum turn", user_id="u", timestamp="t")
    frame.finalized = True
    await tagger.process_frame(frame, None)
    assert frame.speaker_id == "owner"
    # 3 single-turn embeds + 1 accumulation embed on the last turn = 4 calls,
    # and the accumulation call is strictly larger than any single-turn call.
    assert len(call_log) == 4
    assert max(call_log) > len(second_pcm)
    await buffer.close()


async def test_long_turn_uses_single_turn_embedding(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    """SPK2-B2: a turn at/above the accum target ignores the accumulator
    and runs only the single-turn embed.
    """
    import src.speaker_id as sid

    call_log: list[int] = []

    def tracking_embed(pcm, sr, model):
        call_log.append(len(pcm))
        return _owner_vector()

    monkeypatch.setattr(sid, "embed", tracking_embed)
    gallery = _mk_gallery(tmp_path)
    settings = _settings()
    settings.speaker_id_accum_target_ms = 3000
    buffer, tagger = create_speaker_processors(settings, gallery, model=MagicMock())

    # 4-second turn — above accum_target_ms
    long_pcm = b"\x10\x20" * 64000  # 4000ms
    prev = buffer.latest_completed_turn_id()
    await buffer.process_frame(fake_frames["user_start"](), None)
    await buffer.process_frame(
        fake_frames["audio"](audio=long_pcm, sample_rate=16000, num_channels=1),
        None,
    )
    await buffer.process_frame(fake_frames["user_stop"](), None)
    for _ in range(50):
        cur = buffer.latest_completed_turn_id()
        if cur is not None and cur != prev:
            break
        await asyncio.sleep(0.01)

    slot = buffer.get_slot(buffer.latest_completed_turn_id())
    assert slot is not None
    assert slot.duration_ms >= settings.speaker_id_accum_target_ms
    # No second embed for accumulation should have been kicked off
    assert slot.accum_embedding is None
    # Only one embed call was made (single-turn), not two
    assert len(call_log) == 1

    frame = fake_frames["transcript"](text="long turn", user_id="u", timestamp="t")
    frame.finalized = True
    await tagger.process_frame(frame, None)
    assert frame.speaker_id == "owner"
    await buffer.close()


async def test_tagger_auto_appends_high_confidence_long_turn(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    """SPK2-B4: a long, high-confidence owner turn should call
    gallery.append_reference so the centroid adapts over the session.
    """
    import src.speaker_id as sid

    monkeypatch.setattr(sid, "embed", lambda pcm, sr, model: _owner_vector())
    gallery = _mk_gallery(tmp_path)
    call_log: list[tuple[str, int]] = []
    original_append = gallery.append_reference

    def tracking_append(speaker_id, v, **kwargs):
        result = original_append(speaker_id, v, **kwargs)
        call_log.append((speaker_id, len(gallery._speakers[speaker_id]["embeddings"])))
        return result

    gallery.append_reference = tracking_append  # type: ignore[method-assign]
    buffer, tagger = create_speaker_processors(_settings(), gallery, model=MagicMock())

    long_pcm = b"\x10\x20" * 64000  # 4000ms @ 16k — above accum target
    await buffer.process_frame(fake_frames["user_start"](), None)
    await buffer.process_frame(
        fake_frames["audio"](audio=long_pcm, sample_rate=16000, num_channels=1),
        None,
    )
    await buffer.process_frame(fake_frames["user_stop"](), None)
    for _ in range(50):
        if buffer.latest_completed_turn_id() is not None:
            break
        await asyncio.sleep(0.01)

    frame = fake_frames["transcript"](text="довга фраза", user_id="u", timestamp="t")
    frame.finalized = True
    await tagger.process_frame(frame, None)
    assert frame.speaker_id == "owner"
    # Owner vector == enrollment vector, so score is ~1.0 — comfortably above
    # threshold_match + 0.05. append_reference must have been invoked once.
    assert len(call_log) == 1
    assert call_log[0][0] == "owner"
    # Gallery now has 2 references (original enrollment + auto-appended)
    assert call_log[0][1] == 2
    await buffer.close()


async def test_tagger_auto_appends_marginal_duration_turn(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    """Marginal-duration owner matches also auto-append. The gate is
    'high-confidence owner match', not turn length — we always feed the
    stable single-turn embedding regardless of which path identified.
    """
    import src.speaker_id as sid

    monkeypatch.setattr(sid, "embed", lambda pcm, sr, model: _owner_vector())
    gallery = _mk_gallery(tmp_path)
    append_calls: list[str] = []
    original_append = gallery.append_reference

    def tracking_append(speaker_id, v, **kwargs):
        append_calls.append(speaker_id)
        return original_append(speaker_id, v, **kwargs)

    gallery.append_reference = tracking_append  # type: ignore[method-assign]
    buffer, tagger = create_speaker_processors(_settings(), gallery, model=MagicMock())

    # 1-second marginal-duration turn (above min_duration_ms, below accum target)
    pcm = b"\x10\x20" * 16000
    await buffer.process_frame(fake_frames["user_start"](), None)
    await buffer.process_frame(
        fake_frames["audio"](audio=pcm, sample_rate=16000, num_channels=1),
        None,
    )
    await buffer.process_frame(fake_frames["user_stop"](), None)
    for _ in range(50):
        if buffer.latest_completed_turn_id() is not None:
            break
        await asyncio.sleep(0.01)

    frame = fake_frames["transcript"](text="коротка", user_id="u", timestamp="t")
    frame.finalized = True
    await tagger.process_frame(frame, None)
    assert append_calls == ["owner"]
    await buffer.close()


# ---------------------------------------------------------------------------
# SPK-A4: auto-enrollment of repeated non-owner voices
# ---------------------------------------------------------------------------


async def _run_stranger_turn(
    buffer, tagger, fake_frames, turn_text: str, duration_samples: int = 80000
) -> None:
    """Feed one complete non-owner turn (start / audio / stop / transcript).

    duration_samples=80000 -> 5000ms PCM at 16kHz, above the 400ms floor
    and above the 3000ms accum target so the tagger reads slot.embedding
    directly (no accum interference).
    """
    pcm = b"\x10\x20" * duration_samples
    await buffer.process_frame(fake_frames["user_start"](), None)
    await buffer.process_frame(
        fake_frames["audio"](audio=pcm, sample_rate=16000, num_channels=1),
        None,
    )
    await buffer.process_frame(fake_frames["user_stop"](), None)
    target_turn_id = buffer._next_turn_id - 1
    for _ in range(100):
        if buffer.latest_completed_turn_id() == target_turn_id:
            break
        await asyncio.sleep(0.01)
    frame = fake_frames["transcript"](text=turn_text, user_id="u", timestamp="t")
    frame.finalized = True
    await tagger.process_frame(frame, None)


async def test_auto_enroll_disabled_by_default(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    import src.speaker_id as sid

    monkeypatch.setattr(sid, "embed", lambda pcm, sr, model: _stranger_vector())
    gallery = _mk_gallery(tmp_path)
    # speaker_id_auto_enroll_enabled defaults to False — feeding 5 matching
    # stranger turns must not grow the gallery beyond owner.
    buffer, tagger = create_speaker_processors(_settings(), gallery, model=MagicMock())

    for i in range(5):
        await _run_stranger_turn(buffer, tagger, fake_frames, f"стрейнджер {i}")

    assert gallery.list_speakers() == ["owner"]
    await buffer.close()


async def test_auto_enroll_triggers_after_threshold(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    import src.speaker_id as sid

    monkeypatch.setattr(sid, "embed", lambda pcm, sr, model: _stranger_vector())
    gallery = _mk_gallery(tmp_path)
    settings = _settings()
    settings.speaker_id_auto_enroll_enabled = True
    settings.speaker_id_auto_enroll_after = 3
    buffer, tagger = create_speaker_processors(settings, gallery, model=MagicMock())

    # Three matching non-owner turns — the third triggers enroll_guest
    for i in range(3):
        await _run_stranger_turn(buffer, tagger, fake_frames, f"стрейнджер {i}")

    assert "guest_01" in gallery.list_speakers()
    assert "owner" in gallery.list_speakers()
    await buffer.close()


async def test_auto_enroll_ignores_dissimilar_candidates(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    import src.speaker_id as sid

    # Rotate through three orthogonal vectors so no pair reaches the 0.75
    # similarity threshold.
    vectors = [
        np.eye(192, dtype=np.float32)[5],
        np.eye(192, dtype=np.float32)[42],
        np.eye(192, dtype=np.float32)[100],
    ]
    counter = {"i": 0}

    def rotating_embed(pcm, sr, model):
        v = vectors[counter["i"] % len(vectors)]
        counter["i"] += 1
        return v

    monkeypatch.setattr(sid, "embed", rotating_embed)
    gallery = _mk_gallery(tmp_path)
    settings = _settings()
    settings.speaker_id_auto_enroll_enabled = True
    settings.speaker_id_auto_enroll_after = 3
    buffer, tagger = create_speaker_processors(settings, gallery, model=MagicMock())

    for i in range(3):
        await _run_stranger_turn(buffer, tagger, fake_frames, f"стрейнджер {i}")

    # No guest_NN should have been enrolled — all three candidates are
    # mutually orthogonal.
    assert "guest_01" not in gallery.list_speakers()
    assert gallery.list_speakers() == ["owner"]
    await buffer.close()


async def test_auto_enroll_ignores_short_turns(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    import src.speaker_id as sid

    monkeypatch.setattr(sid, "embed", lambda pcm, sr, model: _stranger_vector())
    gallery = _mk_gallery(tmp_path)
    settings = _settings()
    settings.speaker_id_auto_enroll_enabled = True
    settings.speaker_id_auto_enroll_after = 3
    buffer, tagger = create_speaker_processors(settings, gallery, model=MagicMock())

    # 3200 samples @ 16kHz = 200ms, below speaker_id_min_duration_ms=400.
    # The tagger takes the short-turn branch and never reaches auto-enroll.
    for i in range(5):
        await _run_stranger_turn(
            buffer, tagger, fake_frames, f"коротко {i}", duration_samples=1600
        )

    assert "guest_01" not in gallery.list_speakers()
    await buffer.close()


async def test_auto_enroll_clears_buffer_after_success(
    fake_frames, tmp_path: Path, monkeypatch
) -> None:
    import src.speaker_id as sid

    monkeypatch.setattr(sid, "embed", lambda pcm, sr, model: _stranger_vector())
    gallery = _mk_gallery(tmp_path)
    settings = _settings()
    settings.speaker_id_auto_enroll_enabled = True
    settings.speaker_id_auto_enroll_after = 3
    buffer, tagger = create_speaker_processors(settings, gallery, model=MagicMock())

    for i in range(3):
        await _run_stranger_turn(buffer, tagger, fake_frames, f"стрейнджер {i}")

    # Buffer cleared on successful enroll so subsequent stranger turns
    # don't immediately re-enroll a new guest from residue.
    assert len(tagger._stranger_candidates) == 0
    # But the enrolled guest_01 is now in the gallery, so subsequent
    # turns will match it via identify() rather than stack up as candidates.
    await buffer.close()
