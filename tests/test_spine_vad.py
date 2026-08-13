import struct
from dataclasses import dataclass
from typing import Sequence

from src.spine.vad import EnergyVAD, EventKind, SpeechEvent


# Test fixtures: frames at 16kHz mono int16, 20ms each = 320 samples.
SILENCE_FRAME = b"\x00" * 640  # 320 int16 samples of silence


def create_speech_frame() -> bytes:
    """Create a speech frame with square wave amplitude 10000."""
    # Alternate between +10000 and -10000 for 320 samples.
    samples = [10000 if i % 2 == 0 else -10000 for i in range(320)]
    return struct.pack("<320h", *samples)


SPEECH_FRAME = create_speech_frame()


@dataclass
class SyntheticTestCase:
    name: str
    frames: list[bytes]
    expected_events: list[SpeechEvent]


def test_silence_only() -> None:
    """Test case 1: silence only → no events."""
    vad = EnergyVAD()
    # Feed 1 second of silence (50 frames @ 20ms).
    for _ in range(50):
        event = vad.feed(SILENCE_FRAME)
        assert event is None, "Expected no events during silence"


def test_speech_onset() -> None:
    """Test case 2: speech >= start_ms → exactly one START."""
    vad = EnergyVAD(start_ms=120)  # Need 6 frames (120ms / 20ms).
    events = []

    # Feed 6 speech frames (120ms).
    for i in range(6):
        event = vad.feed(SPEECH_FRAME)
        if event:
            events.append(event)

    # Should have exactly one START event.
    assert len(events) == 1, f"Expected 1 event, got {len(events)}"
    assert events[0].kind == EventKind.START, f"Expected START, got {events[0].kind}"
    assert events[0].utterance == b"", "START event should have empty utterance"


def test_speech_then_silence() -> None:
    """Test case 3: speech then >= stop_ms silence → exactly one END; utterance includes preroll."""
    vad = EnergyVAD(
        start_ms=120,  # 6 frames to start
        stop_ms=600,   # 30 frames to stop
        preroll_ms=300,  # 15 frames of preroll
    )
    events = []

    # Feed 3 seconds of silence (150 frames) - fills preroll to 15 frames.
    for _ in range(150):
        event = vad.feed(SILENCE_FRAME)
        if event:
            events.append(event)

    # Feed 10 speech frames.
    for _ in range(10):
        event = vad.feed(SPEECH_FRAME)
        if event:
            events.append(event)

    # Should have START event.
    assert len(events) == 1
    assert events[0].kind == EventKind.START

    # Feed 30 silence frames (600ms) to trigger END.
    for _ in range(30):
        event = vad.feed(SILENCE_FRAME)
        if event:
            events.append(event)

    # Should have exactly 2 events: START and END.
    assert len(events) == 2, f"Expected 2 events, got {len(events)}"
    assert events[1].kind == EventKind.END

    # Utterance should be longer than speech frames alone (includes preroll + pending).
    # Preroll: 15 frames = 9600 bytes
    # Pending: 10 (speech) + 30 (silence trailing) = 40 frames = 25600 bytes
    # Total: 35200 bytes
    expected_length = 15 * 640 + 40 * 640
    assert len(events[1].utterance) == expected_length, (
        f"Expected utterance length {expected_length}, got {len(events[1].utterance)}"
    )
    assert len(events[1].utterance) > 10 * 640, "Utterance should include preroll + silence"


def test_blip_short() -> None:
    """Test case 4: blip < start_ms → no events at all."""
    vad = EnergyVAD(start_ms=120)  # Need 6 frames.
    events = []

    # Feed only 3 speech frames (60ms < 120ms).
    for _ in range(3):
        event = vad.feed(SPEECH_FRAME)
        if event:
            events.append(event)

    # Then silence to reset (but no END event expected since we never STARTed).
    for _ in range(50):
        event = vad.feed(SILENCE_FRAME)
        if event:
            events.append(event)

    assert len(events) == 0, f"Expected 0 events, got {len(events)}"


def test_two_utterances() -> None:
    """Test case 5: two utterances with silence gap → START, END, START, END in order."""
    vad = EnergyVAD(
        start_ms=120,  # 6 frames
        stop_ms=600,   # 30 frames
        preroll_ms=300,  # 15 frames
    )
    events = []

    # First utterance: 200ms of silence (preroll), 150ms of speech, 700ms of silence.
    for _ in range(10):
        event = vad.feed(SILENCE_FRAME)
        if event:
            events.append(event)

    for _ in range(7):  # 7 * 20ms = 140ms (just under start threshold, need 6 frames)
        event = vad.feed(SPEECH_FRAME)
        if event:
            events.append(event)

    for _ in range(2):  # 2 more frames to hit 9 frames total = 180ms
        event = vad.feed(SPEECH_FRAME)
        if event:
            events.append(event)

    # Should have START by now.
    assert len(events) == 1
    assert events[0].kind == EventKind.START

    # Feed 35 silence frames to trigger END (need 30 for stop, 5 extra).
    for _ in range(35):
        event = vad.feed(SILENCE_FRAME)
        if event:
            events.append(event)

    # Should have END now.
    assert len(events) == 2
    assert events[1].kind == EventKind.END

    # Second utterance: 10 silence frames, then 10 speech frames.
    for _ in range(10):
        event = vad.feed(SILENCE_FRAME)
        if event:
            events.append(event)

    for _ in range(10):
        event = vad.feed(SPEECH_FRAME)
        if event:
            events.append(event)

    # Should have START for second utterance.
    assert len(events) == 3
    assert events[2].kind == EventKind.START

    # Feed 35 silence frames to trigger END.
    for _ in range(35):
        event = vad.feed(SILENCE_FRAME)
        if event:
            events.append(event)

    # Should have END for second utterance.
    assert len(events) == 4
    assert events[3].kind == EventKind.END


def test_max_utterance_timeout() -> None:
    """Test case 6: continuous speech past max_utterance_s → forced END."""
    vad = EnergyVAD(
        start_ms=120,  # 6 frames
        stop_ms=600,   # 30 frames
        max_utterance_s=0.5,  # 500ms = 25 frames
    )
    events = []

    # Feed 1 second of silence to fill preroll.
    for _ in range(50):
        event = vad.feed(SILENCE_FRAME)
        if event:
            events.append(event)

    # Feed 35 frames of speech (700ms, exceeds 500ms max).
    # After 6 frames we get START, after 25 total frames we get forced END.
    for i in range(35):
        event = vad.feed(SPEECH_FRAME)
        if event:
            events.append(event)

    # Should have START after 6 frames, then forced END after 25 frames of speech.
    assert len(events) == 2, f"Expected 2 events (START + forced END), got {len(events)}"
    assert events[0].kind == EventKind.START
    assert events[1].kind == EventKind.END


def test_reset_clears_state() -> None:
    """Test case 7: reset() clears mid-speech state."""
    vad = EnergyVAD(start_ms=120)
    events = []

    # Feed 4 speech frames (80ms, not enough to start).
    for _ in range(4):
        event = vad.feed(SPEECH_FRAME)
        if event:
            events.append(event)

    # Reset in the middle.
    vad.reset()

    # Feed more speech frames; should start fresh.
    for _ in range(6):
        event = vad.feed(SPEECH_FRAME)
        if event:
            events.append(event)

    # Should have exactly one START event (from the fresh start).
    assert len(events) == 1, f"Expected 1 event after reset, got {len(events)}"
    assert events[0].kind == EventKind.START


if __name__ == "__main__":
    test_silence_only()
    test_speech_onset()
    test_speech_then_silence()
    test_blip_short()
    test_two_utterances()
    test_max_utterance_timeout()
    test_reset_clears_state()
    print("All tests passed!")


# -- reviewer findings: abandoned onset + pre-STT loudness gate --------


def _speech_frame(amplitude: int = 10000, samples: int = 320) -> bytes:
    import struct as _struct
    return b"".join(
        _struct.pack("<h", amplitude if i % 2 == 0 else -amplitude)
        for i in range(samples)
    )


def test_failed_onset_is_abandoned_not_accumulated() -> None:
    """A blip shorter than start_ms must not leave pending frames behind:
    before the fix, one chair creak made pending_frames grow forever and
    froze the preroll (reviewer finding #2)."""
    vad = EnergyVAD()
    silence = b"\x00" * 640

    for _ in range(20):
        assert vad.feed(silence) is None
    # Two loud frames (40ms) — below start_ms=120 — then silence again.
    for _ in range(2):
        vad.feed(_speech_frame())
    vad.feed(silence)

    assert vad.pending_frames == [], "failed onset must be discarded"
    assert vad.saved_preroll == b"", "stale preroll must be discarded"

    # Long silence after the blip must NOT keep accumulating anywhere.
    for _ in range(100):
        vad.feed(silence)
    assert vad.pending_frames == []

    # A real utterance afterwards is bounded: preroll + speech + trailing,
    # not "everything since the blip".
    events = []
    for _ in range(10):
        e = vad.feed(_speech_frame())
        if e:
            events.append(e)
    for _ in range(31):
        e = vad.feed(silence)
        if e:
            events.append(e)
    kinds = [e.kind.value for e in events]
    assert kinds == ["start", "end"]
    max_reasonable = (15 + 10 + 31) * 640  # preroll ring + speech + trailing
    assert len(events[-1].utterance) <= max_reasonable


def test_loud_ms_measures_only_loud_audio() -> None:
    from src.spine.vad import loud_ms

    silence = b"\x00" * 640
    speech = _speech_frame()

    assert loud_ms(silence * 50) == 0
    assert loud_ms(speech * 10) == 200  # 10 frames x 20ms
    mixed = silence * 20 + speech * 12 + silence * 30
    assert loud_ms(mixed) == 240
