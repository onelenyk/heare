"""Unit tests for the mouth module.

Replaces the speaker with a deterministic fake: a queue where bytes are kept
until explicitly drained, and playing tracks whether the queue is non-empty.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

from src.mouth import Mouth


# -- fake speaker -------------------------------------------------------


class FakeSpeaker:
    """A speaker that keeps queued bytes in memory and never plays them out.

    Tests control when bytes are considered "drained" via drain(). The
    queue is honest: play() adds, stop_playback() returns what's queued,
    and playing is True while the queue is non-empty.
    """

    def __init__(self) -> None:
        self._queue: bytearray = bytearray()
        self.mute_input = False
        self._muted_at: list[bool] = []  # Record of mute_input at each play()

    def play(self, pcm: bytes) -> None:
        """Add bytes to the queue and record the mute state."""
        self._muted_at.append(self.mute_input)
        self._queue.extend(pcm)

    def stop_playback(self) -> int:
        """Return bytes that are queued (dropped) and clear them."""
        dropped = len(self._queue)
        self._queue.clear()
        return dropped

    @property
    def playing(self) -> bool:
        """True while the queue is non-empty."""
        return bool(self._queue)

    def drain(self, how_many: int | None = None) -> None:
        """Drain the queue, removing how_many bytes or all if None."""
        if how_many is None:
            self._queue.clear()
        else:
            del self._queue[:how_many]

    def queued_bytes(self) -> int:
        """How many bytes are currently queued."""
        return len(self._queue)

    def clear_mute_log(self) -> None:
        """Clear the mute tracking log."""
        self._muted_at.clear()


# -- synthesisers -------------------------------------------------------


async def simple_synthesiser(text: str) -> AsyncIterator[bytes]:
    """A simple fake: chunks of 100 bytes per word."""
    words = text.split()
    for word in words:
        yield b"x" * 100  # Each word becomes 100 bytes


async def chunked_synthesiser(text: str) -> AsyncIterator[bytes]:
    """Returns text as bytes, split into 30-byte chunks."""
    data = text.encode()
    for i in range(0, len(data), 30):
        yield data[i : i + 30]


async def two_sentence_synthesiser(text: str) -> AsyncIterator[bytes]:
    """Returns 'A' as 100 bytes, 'B' as 100 bytes."""
    if "B" in text:
        yield b"B" * 50
        yield b"B" * 50
    else:
        yield b"A" * 50
        yield b"A" * 50


# -- tests -------------------------------------------------------


@pytest.mark.asyncio
async def test_say_feeds_chunks_to_speaker_and_returns_when_synthesis_ends() -> None:
    """say() feeds chunks to speaker as they arrive and returns when synthesis ends.

    Would break if: say() waits for playback to finish, or doesn't feed chunks
    incrementally, or returns before synthesis completes.
    """
    speaker = FakeSpeaker()
    mouth = Mouth(speaker)
    utterance = mouth.open(simple_synthesiser, mute_mic=False)

    # say() should return while chunks are still queued
    await utterance.say("hello world")

    # Both words should be queued (200 bytes total)
    assert speaker.queued_bytes() == 200
    # play() was called twice (once per word)
    assert len(speaker._muted_at) == 2


@pytest.mark.asyncio
async def test_stop_mid_synthesis_prevents_further_chunks() -> None:
    """stop("barged") mid-synthesis: no further chunks reach speaker.

    Would break if: chunks continue to be fed after stop(), or stopped flag
    is not checked during synthesis, or stop() doesn't return dropped bytes.
    """
    speaker = FakeSpeaker()
    mouth = Mouth(speaker)

    stop_at_word = 0

    async def stoppable_synthesiser(text: str) -> AsyncIterator[bytes]:
        """Synthesises but can be stopped mid-way."""
        words = text.split()
        for i, word in enumerate(words):
            if i == stop_at_word and i > 0:
                break
            yield b"x" * 100

    utterance = mouth.open(stoppable_synthesiser, mute_mic=False)

    # Start synthesis in the background
    synthesis_task = asyncio.create_task(utterance.say("word one word two word three"))

    # Let the first word synthesise
    await asyncio.sleep(0.01)
    stopped_after_word_one = speaker.queued_bytes()

    # Stop it
    dropped = utterance.stop("barged")

    # Wait for synthesis to finish
    await synthesis_task

    # Verify the state
    assert utterance.stopped
    assert utterance.outcome == "barged"
    assert dropped == stopped_after_word_one
    assert speaker.queued_bytes() == 0  # All dropped


@pytest.mark.asyncio
async def test_pause_stops_speaker_keeps_everything() -> None:
    """pause() stops the speaker and keeps everything not yet heard.

    Would break if: pause() doesn't stop the speaker, or drops buffered audio,
    or doesn't track which sentence was cut.
    """
    speaker = FakeSpeaker()
    mouth = Mouth(speaker)
    utterance = mouth.open(simple_synthesiser, mute_mic=False)

    # Say two sentences
    await utterance.say("first")
    await utterance.say("second")

    # Drain the first sentence (100 bytes)
    speaker.drain(100)

    # Pause
    utterance.pause()

    # Speaker should be silent
    assert not speaker.playing
    # The queue should be cleared by stop_playback()
    assert speaker.queued_bytes() == 0
    # But the utterance should remember everything
    assert len(utterance._sentences) == 2
    assert len(utterance._sentences[0]) == 100
    assert len(utterance._sentences[1]) == 100


@pytest.mark.asyncio
async def test_resume_replays_from_cut_sentence_not_from_start() -> None:
    """resume() replays from the START of the sentence that was cut.

    Would break if: resume() replays from the start of the utterance, or
    skips the cut sentence, or doesn't include buffered audio.
    """
    speaker = FakeSpeaker()
    mouth = Mouth(speaker)
    utterance = mouth.open(simple_synthesiser, mute_mic=False)

    # Say two sentences: A (100 bytes) and B (100 bytes)
    await utterance.say("A")
    await utterance.say("B")

    # Drain part of B (50 bytes)
    speaker.drain(150)  # 100 from A + 50 from B

    # Pause
    utterance.pause()

    assert speaker.queued_bytes() == 0

    # Resume
    utterance.resume()

    # Should replay B from its start (100 bytes), not A or partial B
    assert speaker.queued_bytes() == 100
    # play() should have been called three times total:
    # say("A") -> one play() call
    # say("B") -> one play() call
    # resume() -> one play() call with B replayed
    assert len(speaker._muted_at) == 3


@pytest.mark.asyncio
async def test_resume_after_full_drain_replays_nothing_already_heard() -> None:
    """resume() after a pause following full drain: replays only what came after.

    Would break if: resume() replays audio that was already fully drained,
    or doesn't track what was actually heard before pause.
    """
    speaker = FakeSpeaker()
    mouth = Mouth(speaker)
    utterance = mouth.open(simple_synthesiser, mute_mic=False)

    # Say two sentences
    await utterance.say("first")
    await utterance.say("second")

    # Drain everything
    speaker.drain(200)

    # Pause
    utterance.pause()

    assert speaker.queued_bytes() == 0

    # Resume
    utterance.resume()

    # Nothing should be replayed because everything was already heard
    assert speaker.queued_bytes() == 0


@pytest.mark.asyncio
async def test_finish_returns_done_after_speaker_drains() -> None:
    """finish() returns "done" after the speaker drains.

    Would break if: finish() returns before playback ends, or returns the
    wrong outcome, or doesn't track the outcome correctly.
    """
    speaker = FakeSpeaker()
    mouth = Mouth(speaker)
    utterance = mouth.open(simple_synthesiser, mute_mic=False)

    await utterance.say("hello")

    # Drain while finish() is waiting
    async def drain_soon() -> None:
        await asyncio.sleep(0.05)
        speaker.drain()

    drain_task = asyncio.create_task(drain_soon())

    outcome = await utterance.finish(timeout=1.0)

    await drain_task

    assert outcome == "done"
    assert utterance.outcome == "done"


@pytest.mark.asyncio
async def test_finish_returns_stopped_outcome_when_already_stopped() -> None:
    """finish() returns "barged" or "cancelled" when already stopped.

    Would break if: finish() doesn't respect the outcome set by stop(),
    or overwrites it with "done".
    """
    speaker = FakeSpeaker()
    mouth = Mouth(speaker)
    utterance = mouth.open(simple_synthesiser, mute_mic=False)

    await utterance.say("hello")

    # Stop it
    utterance.stop("barged")

    # finish() should return the stopped outcome
    outcome = await utterance.finish(timeout=1.0)

    assert outcome == "barged"


@pytest.mark.asyncio
async def test_finish_returns_wedged_when_speaker_never_drains() -> None:
    """finish() returns "wedged" and clears queue when speaker never drains.

    Would break if: finish() hangs forever, doesn't clear the queue, or
    doesn't set the outcome to "wedged".
    """
    speaker = FakeSpeaker()
    mouth = Mouth(speaker)
    utterance = mouth.open(simple_synthesiser, mute_mic=False)

    await utterance.say("hello")

    # Don't drain - the speaker will never drain
    assert speaker.queued_bytes() == 100

    # finish() with a tiny timeout should detect this
    outcome = await utterance.finish(timeout=0.05)

    assert outcome == "wedged"
    assert utterance.outcome == "wedged"
    assert speaker.queued_bytes() == 0  # Queue was cleared


@pytest.mark.asyncio
async def test_finish_waits_while_paused() -> None:
    """finish() waits while paused, returns once resumed and drained.

    Would break if: finish() returns while still paused, or doesn't wait
    for resume and drain.
    """
    speaker = FakeSpeaker()
    mouth = Mouth(speaker)
    utterance = mouth.open(simple_synthesiser, mute_mic=False)

    await utterance.say("hello")

    # Pause it
    utterance.pause()

    assert utterance.paused

    # Start finish() in background
    finish_task = asyncio.create_task(utterance.finish(timeout=2.0))

    # Give finish() time to check and wait
    await asyncio.sleep(0.05)

    # finish() should still be waiting (paused)
    assert not finish_task.done()

    # Resume and drain
    utterance.resume()
    speaker.drain()

    # finish() should complete
    outcome = await finish_task

    assert outcome == "done"


@pytest.mark.asyncio
async def test_open_with_mute_mic_true_mutes_at_open() -> None:
    """open(mute_mic=True) mutes the microphone at open before any sound.

    Would break if: mute_input is not set at open time, is not cleared at
    close/finish, or the flag is wrong.
    """
    speaker = FakeSpeaker()
    mouth = Mouth(speaker)

    # Open with mute_mic=True
    utterance = mouth.open(simple_synthesiser, mute_mic=True)

    # Microphone should be muted immediately
    assert speaker.mute_input

    # Say something
    await utterance.say("hello")

    # Should still be muted
    assert speaker.mute_input

    # Close should unmute
    utterance.close()

    assert not speaker.mute_input


@pytest.mark.asyncio
async def test_open_with_mute_mic_false_never_touches_it() -> None:
    """open(mute_mic=False) never touches the microphone.

    Would break if: mute_mic=False still mutes, or state is changed
    incorrectly.
    """
    speaker = FakeSpeaker()
    speaker.mute_input = True  # Start with mute on

    mouth = Mouth(speaker)
    utterance = mouth.open(simple_synthesiser, mute_mic=False)

    # Should still be muted (unchanged)
    assert speaker.mute_input

    await utterance.say("hello")

    # Should still be muted
    assert speaker.mute_input

    # Close should not change it
    utterance.close()

    assert speaker.mute_input


@pytest.mark.asyncio
async def test_finish_unmutes_microphone_on_exit() -> None:
    """finish() unmutes microphone before returning, even on error.

    Would break if: finish() doesn't call close(), or close() doesn't
    unmute when mute_mic=True.
    """
    speaker = FakeSpeaker()
    mouth = Mouth(speaker)

    utterance = mouth.open(simple_synthesiser, mute_mic=True)

    assert speaker.mute_input

    await utterance.say("hello")

    # Drain it
    speaker.drain()

    # finish() should unmute before returning
    await utterance.finish(timeout=1.0)

    assert not speaker.mute_input


@pytest.mark.asyncio
async def test_mouth_stop_all_stops_two_live_utterances() -> None:
    """Mouth.stop_all() stops two live utterances and returns total dropped.

    Would break if: only one utterance is stopped, or dropped bytes aren't
    summed correctly, or utterances aren't tracked.
    """
    speaker = FakeSpeaker()
    mouth = Mouth(speaker)

    # Open two utterances
    u1 = mouth.open(simple_synthesiser, mute_mic=False)
    u2 = mouth.open(simple_synthesiser, mute_mic=False)

    await u1.say("first")
    await u2.say("second")

    # Both have 100 bytes queued
    assert speaker.queued_bytes() == 200

    # Stop all
    total_dropped = mouth.stop_all("barged")

    # Both should be stopped
    assert u1.stopped
    assert u2.stopped
    # Total dropped should be 200
    assert total_dropped == 200
    assert speaker.queued_bytes() == 0


@pytest.mark.asyncio
async def test_mouth_stop_all_with_nothing_live_still_clears_speaker() -> None:
    """Mouth.stop_all() with nothing live still clears speaker and returns dropped.

    Would break if: stop_all() doesn't work when _live is empty, or doesn't
    clear the speaker.
    """
    speaker = FakeSpeaker()
    mouth = Mouth(speaker)

    # Queue some bytes directly (simulating orphaned audio)
    speaker.play(b"x" * 100)

    assert speaker.queued_bytes() == 100

    # Stop all (nothing is live)
    dropped = mouth.stop_all()

    assert dropped == 100
    assert speaker.queued_bytes() == 0


@pytest.mark.asyncio
async def test_mouth_interrupted_describes_latest_utterance_only() -> None:
    """Mouth.interrupted describes the latest utterance only, no latch.

    Would break if: interrupted stays True after a fresh open(), or
    describes an old utterance instead of the last one.
    """
    speaker = FakeSpeaker()
    mouth = Mouth(speaker)

    # Open and stop one
    u1 = mouth.open(simple_synthesiser, mute_mic=False)
    await u1.say("first")
    u1.stop("barged")

    # interrupted should be True (u1 was stopped)
    assert mouth.interrupted

    # Open a fresh one (without stopping it)
    u2 = mouth.open(simple_synthesiser, mute_mic=False)

    # interrupted should now be False (u2 is not stopped)
    assert not mouth.interrupted

    # Stop u2
    u2.stop("cancelled")

    # interrupted should be True again
    assert mouth.interrupted


@pytest.mark.asyncio
async def test_close_is_idempotent_and_forgets_utterance() -> None:
    """close() is idempotent and removes utterance from mouth's live list.

    Would break if: close() can't be called twice, or doesn't remove from
    _live, or doesn't clear sentences.
    """
    speaker = FakeSpeaker()
    mouth = Mouth(speaker)

    utterance = mouth.open(simple_synthesiser, mute_mic=True)

    # Verify it's in the live list
    assert utterance in mouth._live

    # Say something
    await utterance.say("hello")

    # Sentences should be stored
    assert len(utterance._sentences) == 1

    # Close
    utterance.close()

    # Should be removed from live list
    assert utterance not in mouth._live
    # Sentences should be cleared
    assert len(utterance._sentences) == 0
    # Should be unmuted
    assert not speaker.mute_input

    # Close again (should be idempotent)
    utterance.close()

    assert utterance not in mouth._live
    assert not speaker.mute_input


@pytest.mark.asyncio
async def test_paused_chunks_are_kept_but_not_played() -> None:
    """Chunks synthesized while paused are kept but not played.

    Would break if: paused chunks are dropped, or still reach the speaker,
    or the paused flag doesn't prevent play().
    """
    speaker = FakeSpeaker()
    mouth = Mouth(speaker)
    utterance = mouth.open(simple_synthesiser, mute_mic=False)

    # Say and drain first sentence
    await utterance.say("first")
    speaker.drain(100)

    # Pause
    utterance.pause()

    # Say second sentence while paused
    await utterance.say("second")

    # Second sentence should be in _sentences
    assert len(utterance._sentences) == 2
    assert len(utterance._sentences[1]) == 100

    # But not in the speaker's queue (because it was paused)
    assert speaker.queued_bytes() == 0


@pytest.mark.asyncio
async def test_mouth_speaking_property() -> None:
    """Mouth.speaking indicates whether audio is currently playing.

    Would break if: speaking doesn't reflect actual playback state, or
    doesn't check the speaker's playing property.
    """
    speaker = FakeSpeaker()
    mouth = Mouth(speaker)

    # No utterances yet
    assert not mouth.speaking

    utterance = mouth.open(simple_synthesiser, mute_mic=False)

    # Speaking should be False (nothing in the queue)
    assert not mouth.speaking

    await utterance.say("hello")

    # Now something is queued
    assert speaker.playing
    assert mouth.speaking

    # Drain it
    speaker.drain()

    # Now nothing is playing
    assert not speaker.playing
    assert not mouth.speaking


@pytest.mark.asyncio
async def test_multiple_sentences_in_one_utterance() -> None:
    """Multiple sentences in one utterance are stored and replayed correctly.

    Would break if: sentences aren't tracked independently, or pause/resume
    doesn't find the right sentence boundary.
    """
    speaker = FakeSpeaker()
    mouth = Mouth(speaker)
    utterance = mouth.open(simple_synthesiser, mute_mic=False)

    # Say three sentences
    await utterance.say("one")
    await utterance.say("two")
    await utterance.say("three")

    # All three should be queued
    assert speaker.queued_bytes() == 300
    assert len(utterance._sentences) == 3

    # Drain the first two
    speaker.drain(200)

    # Pause in the middle of the third
    utterance.pause()

    # Verify resume_from is set to index 2 (third sentence)
    assert utterance._resume_from == 2

    # Resume should replay from sentence 2
    utterance.resume()

    # Should have the third sentence in the queue
    assert speaker.queued_bytes() == 100
