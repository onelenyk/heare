"""Nodding and barge-in: pause at once, decide later (design.html §6).

The two-stage interruption:
1. As soon as the user starts speaking while the mouth is live, pause it (~10ms).
2. After ~0.6 seconds, decide based on speech duration and content:
   - Short utterance (< 0.6s): wait for recognition, decide by text
   - Long utterance (≥ 0.6s): stop speaking immediately (outcome="barged")
   - Commands like "стоп": stop (outcome="cancelled" or "barged")
   - Acknowledgments like "ага": resume speaking

Tests marked `@pytest.mark.xfail(reason="two-stage interruption: step 2")` need
the ear to recognize short vs. long speech and command words. The mouth side
(pause/resume/stop) is implemented; the ear's decision is future work.
"""

from __future__ import annotations

import pytest

from tests.e2e.room import close_room, open_room, real_speech

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


@pytest.fixture
async def room(tmp_path):
    r = await open_room(tmp_path)
    try:
        yield r
    finally:
        await close_room(r)


# ─── Must pass today ────────────────────────────────────────────────


async def test_long_speech_stops_the_speaker(room) -> None:
    """Talking for 800 ms over the speaker stops it immediately.

    This is step 1 of the two-stage decision: the mouth pauses at once
    when speech starts, and if speech continues past ~0.6 s, the turn
    is cancelled with outcome="barged". No recognition needed.
    """
    room.will_say("Це буде дуже довга репліка, якоїзавто скасована.")

    await room.told("Дока, почни відповідь")
    await room.spoken("довга фраза яка перебиває", ms=800)

    assert room.outcome() == "barged"
    assert room.cuts_in is True


# ─── Future: need two-stage ear decision ────────────────────────────


@pytest.mark.xfail(reason="two-stage interruption: step 2", strict=True)
async def test_nod_pauses_and_resumes(room) -> None:
    """A nod («ага», ~250 ms) pauses the speaker and reply resumes.

    Short speech is recognized, and if it is an acknowledgment like «ага»,
    the speaker resumes from the start of the sentence that was cut.
    The utterance ends with outcome="done".
    """
    room.will_say(
        "Це дуже довга репліка, яка буде перервана, але потім повторена "
        "від поточного речення, і закінчена нормально в кінці."
    )

    await room.told("Дока, почни довгу репліку")
    # Pause with a short acknowledgment
    await room.nods("ага", ms=250)

    # The reply should have been paused, then resumed
    assert room.paused() is False, "should have resumed by now"
    assert room.outcome() == "done"
    assert room.cuts_in is False


@pytest.mark.xfail(reason="two-stage interruption: step 2", strict=True)
async def test_real_interruption_ends_with_barged(room) -> None:
    """A real interruption (≥ 600 ms) ends the utterance with outcome='barged'.

    This is the second decision in step 2: if speech lasts longer than ~0.6 s,
    stop speaking and cancel the turn.
    """
    room.will_say("Почни, але дозволи перебити довгою фразою")

    await room.told("Дока, почни")
    await room.spoken("це довга фраза яка точно перебиває", ms=700)

    assert room.outcome() == "barged"
    assert room.cuts_in is True


@pytest.mark.xfail(reason="two-stage interruption: step 2", strict=True)
async def test_command_word_stops_immediately(room) -> None:
    """A command word like «стоп» stops the utterance immediately.

    «Стоп» is a command, not an acknowledgment, so the turn is cancelled
    with outcome="cancelled" or "barged" (depending on implementation).
    """
    room.will_say("Починаю розповідати, але зупиняюсь на слові стоп")

    await room.told("Дока, почни")
    # Say "стоп" - should be recognized as a command
    await room.spoken("стоп", ms=400)

    # The utterance should be stopped
    assert room.outcome() in ("cancelled", "barged")
    assert room.cuts_in is True


# ─── Live measurements with real speech ──────────────────────────────


@pytest.mark.spine_live
async def test_speech_duration_measurements(room) -> None:
    """Measure real speech durations to inform the 0.6 s threshold.

    This test runs only when marked explicitly (not in default test suite).
    It measures how long real Ukrainian speech actually takes for common
    short utterances, to calibrate the decision threshold in design §6.
    """
    measurements = []

    phrases = [
        ("ага", "nod"),
        ("угу", "nod"),
        ("так-так", "acknowledgment"),
        ("стоп", "command"),
        ("далі", "command"),
        ("а скільки?", "question"),
    ]

    # Try to generate real speech, skip if unavailable
    try:
        pcm = real_speech("ага")
    except Exception:
        pytest.skip("real_speech unavailable")

    from src.spine.vad import EnergyVAD

    vad = EnergyVAD()

    for phrase, kind in phrases:
        try:
            pcm = real_speech(phrase)
        except Exception:
            pytest.skip(f"Could not generate speech for '{phrase}'")
            continue

        # Feed PCM through VAD to measure duration
        frame_size = 320  # 20 ms at 16 kHz
        start_time = None
        end_time = None

        for i in range(0, len(pcm), frame_size):
            frame = pcm[i : i + frame_size]
            if len(frame) < frame_size:
                break

            event = vad.feed(frame)
            time_ms = (i // 2) / 16  # Convert bytes to ms at 16 kHz

            if event and event.kind.value == "start":
                if start_time is None:
                    start_time = time_ms
            elif event and event.kind.value == "end":
                end_time = time_ms

        if start_time is not None and end_time is not None:
            duration_ms = end_time - start_time
            measurements.append((phrase, kind, duration_ms))

    # Print results in a table
    if measurements:
        print("\n" + "=" * 60)
        print("Speech Duration Measurements (16 kHz, EnergyVAD)")
        print("=" * 60)
        print(f"{'Phrase':<20} {'Type':<20} {'Duration (ms)':<15}")
        print("-" * 60)
        for phrase, kind, duration in measurements:
            print(f"{phrase:<20} {kind:<20} {duration:>8.0f} ms")
        print("=" * 60)

        # Suggest threshold based on measurements
        nod_durations = [d for p, k, d in measurements if k == "nod"]
        if nod_durations:
            max_nod = max(nod_durations)
            print(f"\nMax nod duration: {max_nod:.0f} ms")
            print(f"Suggested threshold: ~{min(600, max_nod * 2):.0f} ms")
            print("(Current design assumes 600 ms)\n")
