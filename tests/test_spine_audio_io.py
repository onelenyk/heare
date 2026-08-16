"""Tests for src.spine.audio_io.AudioIO.

Headless tests with no real audio hardware. Callbacks are invoked directly
with synthetic buffers using memoryview over bytearray.
"""

import asyncio
import pytest
from src.spine.audio_io import AudioIO


class TestAudioIOCallbacks:
    """Test input and output callbacks with synthetic buffers."""

    def test_input_callback_puts_frame_in_queue(self) -> None:
        """Input callback should place frame bytes in the queue."""
        audio_io = AudioIO(input_rate=16000, output_rate=24000, frame_ms=20)

        # Simulate the queue being drained by setting a running loop
        loop = asyncio.new_event_loop()
        audio_io._loop = loop

        # Create a synthetic 20 ms frame at 16 kHz (640 bytes)
        frame_bytes = (16000 * 20) // 1000 * 2  # 640 bytes
        indata = bytearray(b"\x00" * frame_bytes)

        # Call the callback
        audio_io._on_input(indata, frame_bytes // 2, None, None)

        # The frame should be queued (via call_soon_threadsafe logic)
        # Since we're not actually running the loop, we need to pump it
        loop.run_until_complete(asyncio.sleep(0))

        # Check queue
        assert not audio_io.input_frames.empty()
        queued = audio_io.input_frames.get_nowait()
        assert queued == bytes(indata)
        loop.close()

    def test_input_callback_mutes_when_flag_set(self) -> None:
        """Input callback should discard frames when mute_input is True."""
        audio_io = AudioIO(input_rate=16000, output_rate=24000, frame_ms=20)
        loop = asyncio.new_event_loop()
        audio_io._loop = loop
        audio_io.mute_input = True

        # Create a synthetic frame
        frame_bytes = (16000 * 20) // 1000 * 2  # 640 bytes
        indata = bytearray(b"\x00" * frame_bytes)

        # Call the callback
        audio_io._on_input(indata, frame_bytes // 2, None, None)

        # Queue should be empty (frame was discarded)
        assert audio_io.input_frames.empty()
        loop.close()

    def test_input_callback_drops_when_queue_full(self) -> None:
        """Input callback should drop silently when queue is full."""
        audio_io = AudioIO(input_rate=16000, output_rate=24000, frame_ms=20)
        loop = asyncio.new_event_loop()
        audio_io._loop = loop

        # Fill the queue to maxsize
        frame_bytes = (16000 * 20) // 1000 * 2  # 640 bytes
        for _ in range(audio_io.input_frames.maxsize):
            audio_io.input_frames.put_nowait(b"\x00" * frame_bytes)

        # Create a synthetic frame
        indata = bytearray(b"\x01" * frame_bytes)

        # Call callback with full queue - should not raise, should drop
        audio_io._on_input(indata, frame_bytes // 2, None, None)

        # Queue should still be full with the old data
        assert audio_io.input_frames.full()
        first_frame = audio_io.input_frames.get_nowait()
        assert first_frame == b"\x00" * frame_bytes  # Original frame, not new one
        loop.close()

    def test_output_callback_fills_from_buffer(self) -> None:
        """Output callback should copy from buffer to outdata."""
        audio_io = AudioIO(input_rate=16000, output_rate=24000, frame_ms=20)

        # Prepare output buffer
        frame_bytes = (24000 * 20) // 1000 * 2  # 960 bytes
        test_pcm = b"\x42" * frame_bytes
        audio_io._output_buffer.extend(test_pcm)

        # Create a writable buffer (memoryview over bytearray)
        outdata_backing = bytearray(frame_bytes)
        outdata = memoryview(outdata_backing)

        # Call callback
        audio_io._on_output(outdata, frame_bytes // 2, None, None)

        # outdata should be filled with test data
        assert bytes(outdata) == test_pcm
        # Buffer should be empty
        assert len(audio_io._output_buffer) == 0

    def test_output_callback_pads_silence_when_partial(self) -> None:
        """Output callback should pad with silence when buffer is partial."""
        audio_io = AudioIO(input_rate=16000, output_rate=24000, frame_ms=20)

        # Prepare partial output buffer (half the frame size)
        frame_bytes = (24000 * 20) // 1000 * 2  # 960 bytes
        partial_pcm = b"\x42" * (frame_bytes // 2)
        audio_io._output_buffer.extend(partial_pcm)

        # Create a writable buffer
        outdata_backing = bytearray(frame_bytes)
        outdata = memoryview(outdata_backing)

        # Call callback
        audio_io._on_output(outdata, frame_bytes // 2, None, None)

        # First half should be test data, second half silence
        assert bytes(outdata[:len(partial_pcm)]) == partial_pcm
        assert bytes(outdata[len(partial_pcm):]) == b"\x00" * (frame_bytes - len(partial_pcm))
        # Buffer should be empty
        assert len(audio_io._output_buffer) == 0

    def test_output_callback_pads_full_silence_when_empty(self) -> None:
        """Output callback should pad full silence when buffer is empty."""
        audio_io = AudioIO(input_rate=16000, output_rate=24000, frame_ms=20)

        # Buffer is empty
        assert len(audio_io._output_buffer) == 0

        # Create a writable buffer
        frame_bytes = (24000 * 20) // 1000 * 2  # 960 bytes
        outdata_backing = bytearray(frame_bytes)
        outdata = memoryview(outdata_backing)

        # Call callback
        audio_io._on_output(outdata, frame_bytes // 2, None, None)

        # Should be all silence
        assert bytes(outdata) == b"\x00" * frame_bytes
        # Buffer should still be empty
        assert len(audio_io._output_buffer) == 0


class TestAudioIOPlayback:
    """Test play, playing, and stop_playback methods."""

    def test_play_appends_to_buffer(self) -> None:
        """play() should append PCM to the output buffer."""
        audio_io = AudioIO()
        pcm1 = b"\x01" * 100
        pcm2 = b"\x02" * 100

        audio_io.play(pcm1)
        assert bytes(audio_io._output_buffer) == pcm1

        audio_io.play(pcm2)
        assert bytes(audio_io._output_buffer) == pcm1 + pcm2

    def test_playing_true_when_buffer_nonempty(self) -> None:
        """playing should return True when buffer has data."""
        audio_io = AudioIO()
        assert audio_io.playing is False

        audio_io.play(b"\x00" * 100)
        assert audio_io.playing is True

    def test_playing_false_when_buffer_empty(self) -> None:
        """playing should return False when buffer is empty."""
        audio_io = AudioIO()
        audio_io.play(b"\x00" * 100)
        assert audio_io.playing is True

        audio_io._output_buffer.clear()
        assert audio_io.playing is False

    def test_stop_playback_clears_buffer(self) -> None:
        """stop_playback() should clear buffer and return bytes dropped."""
        audio_io = AudioIO()
        pcm = b"\x42" * 500
        audio_io.play(pcm)

        dropped = audio_io.stop_playback()
        assert dropped == 500
        assert len(audio_io._output_buffer) == 0
        assert audio_io.playing is False

    def test_stop_playback_returns_zero_when_empty(self) -> None:
        """stop_playback() should return 0 when buffer is already empty."""
        audio_io = AudioIO()
        dropped = audio_io.stop_playback()
        assert dropped == 0


class TestAudioIOAsyncInit:
    """Test async start and stop (without opening real streams)."""

    @pytest.mark.asyncio
    async def test_start_captures_loop(self) -> None:
        """start() should capture the running event loop."""
        audio_io = AudioIO()
        assert audio_io._loop is None

        # Mock sounddevice to avoid opening real streams
        import sys
        from unittest.mock import MagicMock, patch

        mock_sounddevice = MagicMock()
        mock_stream = MagicMock()
        mock_sounddevice.RawInputStream.return_value = mock_stream
        mock_sounddevice.RawOutputStream.return_value = mock_stream

        with patch.dict(sys.modules, {"sounddevice": mock_sounddevice}):
            await audio_io.start()

        assert audio_io._loop is not None
        assert audio_io._loop == asyncio.get_running_loop()

    @pytest.mark.asyncio
    async def test_stop_closes_streams(self) -> None:
        """stop() should close streams and clear loop reference."""
        audio_io = AudioIO()

        # Mock sounddevice
        import sys
        from unittest.mock import MagicMock, patch

        mock_sounddevice = MagicMock()
        mock_stream = MagicMock()
        mock_sounddevice.RawInputStream.return_value = mock_stream
        mock_sounddevice.RawOutputStream.return_value = mock_stream

        with patch.dict(sys.modules, {"sounddevice": mock_sounddevice}):
            await audio_io.start()
            assert audio_io._loop is not None
            await audio_io.stop()

        assert audio_io._loop is None
        assert audio_io._input_stream is None
        assert audio_io._output_stream is None


class TestAudioIOFullDuplexGating:
    """Test half-duplex gating: mute_input controls whether input is captured."""

    @pytest.mark.asyncio
    async def test_mute_input_during_playback(self) -> None:
        """During playback, mute_input should prevent input from being queued."""
        audio_io = AudioIO()

        # Simulate a running loop
        loop = asyncio.get_running_loop()
        audio_io._loop = loop

        # Start with unmuted input
        frame_bytes = (16000 * 20) // 1000 * 2  # 640 bytes
        indata1 = bytearray(b"\x01" * frame_bytes)
        audio_io._on_input(indata1, frame_bytes // 2, None, None)
        await asyncio.sleep(0)  # Let callbacks run
        assert not audio_io.input_frames.empty()

        # Clear queue
        audio_io.input_frames.get_nowait()

        # Now mute and try again
        audio_io.mute_input = True
        indata2 = bytearray(b"\x02" * frame_bytes)
        audio_io._on_input(indata2, frame_bytes // 2, None, None)
        await asyncio.sleep(0)
        assert audio_io.input_frames.empty()

        # Unmute and verify input works again
        audio_io.mute_input = False
        indata3 = bytearray(b"\x03" * frame_bytes)
        audio_io._on_input(indata3, frame_bytes // 2, None, None)
        await asyncio.sleep(0)
        assert not audio_io.input_frames.empty()


class TestAudioIOBufferOrdering:
    """Test that output buffer is consumed in FIFO order."""

    def test_output_fifo_order(self) -> None:
        """Output callback should consume buffer in FIFO order."""
        audio_io = AudioIO()

        # Add multiple frames to buffer
        frame_bytes = (24000 * 20) // 1000 * 2  # 960 bytes
        pcm1 = b"\x01" * frame_bytes
        pcm2 = b"\x02" * frame_bytes
        audio_io.play(pcm1)
        audio_io.play(pcm2)

        # Consume first frame
        outdata1_backing = bytearray(frame_bytes)
        outdata1 = memoryview(outdata1_backing)
        audio_io._on_output(outdata1, frame_bytes // 2, None, None)
        assert bytes(outdata1) == pcm1

        # Consume second frame
        outdata2_backing = bytearray(frame_bytes)
        outdata2 = memoryview(outdata2_backing)
        audio_io._on_output(outdata2, frame_bytes // 2, None, None)
        assert bytes(outdata2) == pcm2

        # No more data
        assert len(audio_io._output_buffer) == 0


class TestAudioIOFrameParameters:
    """Test that frame sizes are calculated correctly."""

    def test_frame_size_calculation(self) -> None:
        """Frame sizes should be calculated based on sample rate and frame_ms."""
        audio_io = AudioIO(input_rate=16000, output_rate=24000, frame_ms=20)

        # Input: 16000 Hz * 20 ms / 1000 = 320 samples = 640 bytes
        assert audio_io.input_frame_bytes == 640

        # Output: 24000 Hz * 20 ms / 1000 = 480 samples = 960 bytes
        assert audio_io.output_frame_bytes == 960

    def test_custom_frame_ms(self) -> None:
        """Custom frame_ms should affect frame size."""
        audio_io = AudioIO(input_rate=16000, output_rate=24000, frame_ms=10)

        # Input: 16000 Hz * 10 ms / 1000 = 160 samples = 320 bytes
        assert audio_io.input_frame_bytes == 320

        # Output: 24000 Hz * 10 ms / 1000 = 240 samples = 480 bytes
        assert audio_io.output_frame_bytes == 480


class TestAudioIOThreadSafety:
    """Test thread-safety properties."""

    def test_output_buffer_lock_protects_play(self) -> None:
        """play() should acquire lock before modifying buffer."""
        audio_io = AudioIO()

        # The lock should exist
        assert hasattr(audio_io, "_output_lock")
        assert audio_io._output_lock is not None

        # play() should work and modify buffer
        pcm = b"\x42" * 100
        audio_io.play(pcm)
        assert bytes(audio_io._output_buffer) == pcm

    def test_output_buffer_lock_protects_playing(self) -> None:
        """playing property should acquire lock before checking buffer."""
        audio_io = AudioIO()

        # Lock exists
        assert audio_io._output_lock is not None

        # playing should check buffer safely
        assert audio_io.playing is False
        audio_io.play(b"\x00" * 100)
        assert audio_io.playing is True

    def test_output_buffer_lock_protects_stop_playback(self) -> None:
        """stop_playback() should acquire lock before clearing buffer."""
        audio_io = AudioIO()
        audio_io.play(b"\x42" * 100)

        # stop_playback should work and clear safely
        dropped = audio_io.stop_playback()
        assert dropped == 100
        assert len(audio_io._output_buffer) == 0


# -- the user's own switches (dashboard buttons) -----------------------


def test_user_mute_survives_the_conductor_toggling_mute_input() -> None:
    """The half-duplex path flips mute_input on every reply; if it shared
    a flag with the dashboard button, the mic would come back on by
    itself right after the assistant finished speaking."""
    io = AudioIO()
    io.mute_input_user = True   # the user pressed "mic muted"

    io.mute_input = True        # assistant starts speaking (half duplex)
    io.mute_input = False       # assistant finished

    frames: list[bytes] = []

    class _ImmediateLoop:
        def call_soon_threadsafe(self, fn, *args):
            fn(*args)

    io._loop = _ImmediateLoop()  # type: ignore[assignment]
    io.input_frames.put_nowait = frames.append  # type: ignore[method-assign]
    io._on_input(b"\x01\x02" * 320, 320, None, None)
    assert frames == [], "a user-muted mic must stay muted"

    io.mute_input_user = False
    io._on_input(b"\x01\x02" * 320, 320, None, None)
    assert len(frames) == 1, "unmuting restores the mic"


def _pcm(samples: list[int]) -> bytes:
    """Little-endian int16 bytes from a list of sample values."""
    import struct

    return struct.pack(f"<{len(samples)}h", *samples)


def _unpcm(data: bytes) -> list[int]:
    import struct

    return list(struct.unpack(f"<{len(data) // 2}h", data))


class _ImmediateLoop:
    def call_soon_threadsafe(self, fn, *args):
        fn(*args)


def _capture_input(io: AudioIO, data: bytes) -> list[bytes]:
    frames: list[bytes] = []
    io._loop = _ImmediateLoop()  # type: ignore[assignment]
    io.input_frames.put_nowait = frames.append  # type: ignore[method-assign]
    io._on_input(data, len(data) // 2, None, None)
    return frames


class TestInputGain:
    """The mic slider: State `input_gain` had no reader on the spine."""

    def test_gain_one_is_byte_identical_passthrough(self) -> None:
        io = AudioIO()
        assert io.input_gain == 1.0
        data = _pcm([0, 1, -1, 1234, -4321, 32767, -32768])
        frames = _capture_input(io, data)
        assert frames == [data]

    def test_gain_two_doubles_samples(self) -> None:
        io = AudioIO()
        io.input_gain = 2.0
        frames = _capture_input(io, _pcm([0, 100, -100, 3000, -3000]))
        assert _unpcm(frames[0]) == [0, 200, -200, 6000, -6000]

    def test_gain_clamps_instead_of_wrapping(self) -> None:
        """An int16 overflow once destroyed the AEC; loud must stay loud,
        never flip to a large negative sample."""
        io = AudioIO()
        io.input_gain = 4.0
        frames = _capture_input(io, _pcm([32767, 32000, -32000, -32768]))
        out = _unpcm(frames[0])
        assert out == [32767, 32767, -32768, -32768]
        assert all(s >= 0 for s in out[:2]), "positive samples must not wrap"

    def test_gain_zero_silences_the_mic(self) -> None:
        io = AudioIO()
        io.input_gain = 0.0
        frames = _capture_input(io, _pcm([5000, -5000, 32767]))
        assert _unpcm(frames[0]) == [0, 0, 0]


class TestOutputVolume:
    """The speaker slider: State `output_volume`, same dead wire."""

    def _render(self, io: AudioIO, pcm: bytes) -> bytes:
        io._output_buffer.extend(pcm)
        backing = bytearray(len(pcm))
        io._on_output(memoryview(backing), len(pcm) // 2, None, None)
        return bytes(backing)

    def test_volume_one_is_byte_identical_passthrough(self) -> None:
        io = AudioIO()
        assert io.output_volume == 1.0
        pcm = _pcm([0, 1, -1, 9999, -9999, 32767, -32768])
        assert self._render(io, pcm) == pcm

    def test_volume_half_halves_samples(self) -> None:
        io = AudioIO()
        io.output_volume = 0.5
        out = self._render(io, _pcm([0, 100, -100, 30000, -30000]))
        assert _unpcm(out) == [0, 50, -50, 15000, -15000]

    def test_volume_zero_silences(self) -> None:
        io = AudioIO()
        io.output_volume = 0.0
        out = self._render(io, _pcm([32767, -32768, 1234]))
        assert _unpcm(out) == [0, 0, 0]

    def test_volume_clamps_instead_of_wrapping(self) -> None:
        io = AudioIO()
        io.output_volume = 4.0
        out = _unpcm(self._render(io, _pcm([32767, 20000, -20000, -32768])))
        assert out == [32767, 32767, -32768, -32768]

    def test_volume_applies_to_partial_buffer_and_pads_silence(self) -> None:
        io = AudioIO()
        io.output_volume = 2.0
        io._output_buffer.extend(_pcm([100, -100]))
        backing = bytearray(8)  # room for four samples, two available
        io._on_output(memoryview(backing), 4, None, None)
        assert _unpcm(bytes(backing)) == [200, -200, 0, 0]
        assert len(io._output_buffer) == 0

    def test_volume_still_drains_the_buffer_when_silent(self) -> None:
        """Volume 0 must consume playback, not stall it forever."""
        io = AudioIO()
        io.output_volume = 0.0
        io.play(_pcm([1000] * 8))
        backing = bytearray(8)
        io._on_output(memoryview(backing), 4, None, None)
        assert _unpcm(bytes(backing)) == [0, 0, 0, 0]
        assert len(io._output_buffer) == 8  # four of eight samples consumed


class TestDeviceSelection:
    """The device pickers wrote a file nothing on the spine opened."""

    def test_devices_default_to_none(self) -> None:
        io = AudioIO()
        assert io.input_device is None
        assert io.output_device is None

    def test_devices_are_stored(self) -> None:
        io = AudioIO(input_device=3, output_device="Speakers")
        assert io.input_device == 3
        assert io.output_device == "Speakers"

    @pytest.mark.asyncio
    async def test_devices_are_passed_to_sounddevice(self) -> None:
        import sys
        from unittest.mock import MagicMock, patch

        mock_sd = MagicMock()
        mock_sd.RawInputStream.return_value = MagicMock()
        mock_sd.RawOutputStream.return_value = MagicMock()

        io = AudioIO(input_device=2, output_device=7)
        with patch.dict(sys.modules, {"sounddevice": mock_sd}):
            await io.start()

        assert mock_sd.RawInputStream.call_args.kwargs["device"] == 2
        assert mock_sd.RawOutputStream.call_args.kwargs["device"] == 7

    @pytest.mark.asyncio
    async def test_none_devices_are_passed_through_as_none(self) -> None:
        import sys
        from unittest.mock import MagicMock, patch

        mock_sd = MagicMock()
        mock_sd.RawInputStream.return_value = MagicMock()
        mock_sd.RawOutputStream.return_value = MagicMock()

        io = AudioIO()
        with patch.dict(sys.modules, {"sounddevice": mock_sd}):
            await io.start()

        assert mock_sd.RawInputStream.call_args.kwargs["device"] is None
        assert mock_sd.RawOutputStream.call_args.kwargs["device"] is None


def test_user_output_mute_drops_playback() -> None:
    io = AudioIO()
    io.mute_output_user = True
    io.play(b"\x01\x02" * 100)
    assert io.playing is False

    io.mute_output_user = False
    io.play(b"\x01\x02" * 100)
    assert io.playing is True


# -- muting the mic must announce the hole it leaves -------------------


class TestInputGap:
    """A muted microphone is not a quiet microphone: frames stop arriving
    entirely, and the segmenter downstream freezes mid-utterance unless
    somebody says so."""

    def test_no_gap_on_a_fresh_object(self) -> None:
        io = AudioIO()
        assert io.take_input_gap() is False

    def test_mute_input_records_a_gap(self) -> None:
        io = AudioIO()
        io.mute_input = True
        assert io.take_input_gap() is True

    def test_user_mute_records_a_gap(self) -> None:
        io = AudioIO()
        io.mute_input_user = True
        assert io.take_input_gap() is True

    def test_gap_is_consumed_exactly_once(self) -> None:
        io = AudioIO()
        io.mute_input = True
        assert io.take_input_gap() is True
        assert io.take_input_gap() is False

    def test_gap_survives_the_whole_mute(self) -> None:
        """The consumer only sees frames again after unmute; the flag has
        to still be there then, however long the mute lasted."""
        io = AudioIO()
        io.mute_input_user = True
        io.mute_input_user = False
        assert io.take_input_gap() is True

    def test_unmuting_alone_records_nothing(self) -> None:
        io = AudioIO()
        io.mute_input = False
        assert io.take_input_gap() is False

    def test_repeated_mute_is_not_an_edge(self) -> None:
        io = AudioIO()
        io.mute_input = True
        io.take_input_gap()
        io.mute_input = True
        assert io.take_input_gap() is False

    def test_note_input_gap_is_callable_directly(self) -> None:
        io = AudioIO()
        io.note_input_gap()
        assert io.take_input_gap() is True

    def test_mute_flags_stay_independent(self) -> None:
        """The half-duplex toggle must not clear the user's own switch."""
        io = AudioIO()
        io.mute_input_user = True
        io.mute_input = True
        io.mute_input = False
        assert io.mute_input_user is True
        assert io.mute_input is False


# -- the far-end reference must match what the speaker emitted ---------


class _RecordingSink:
    """Stands in for SpineAEC: push_far(pcm) + clear()."""

    def __init__(self) -> None:
        self.pushed: list[bytes] = []
        self.clears = 0

    def push_far(self, pcm: bytes) -> None:
        self.pushed.append(pcm)

    def clear(self) -> None:
        self.clears += 1


class TestFarEndSink:
    def test_default_is_no_sink(self) -> None:
        io = AudioIO()
        assert io.far_sink is None
        io.play(b"\x01\x02" * 100)   # must not explode without a sink
        assert io.stop_playback() == 200

    def test_playback_reaches_buffer_and_sink_exactly_once(self) -> None:
        sink = _RecordingSink()
        io = AudioIO(far_sink=sink)
        pcm = b"\x01\x02" * 100
        io.play(pcm)
        assert bytes(io._output_buffer) == pcm
        assert sink.pushed == [pcm]

    def test_muted_playback_reaches_neither(self) -> None:
        """The bot is muted: no speaker output, so the canceller must not
        be told there was any — it would subtract that phantom echo from
        the user's own voice."""
        sink = _RecordingSink()
        io = AudioIO(far_sink=sink)
        io.mute_output_user = True
        io.play(b"\x01\x02" * 100)
        assert sink.pushed == []
        assert io.playing is False

    def test_policy_mute_also_blocks_the_sink(self) -> None:
        sink = _RecordingSink()
        io = AudioIO(far_sink=sink)
        io.mute_output = True
        io.play(b"\x01\x02" * 100)
        assert sink.pushed == []

    def test_unmuting_resumes_the_reference(self) -> None:
        sink = _RecordingSink()
        io = AudioIO(far_sink=sink)
        io.mute_output_user = True
        io.play(b"\xaa\xbb" * 10)
        io.mute_output_user = False
        io.play(b"\xcc\xdd" * 10)
        assert sink.pushed == [b"\xcc\xdd" * 10]

    def test_stop_playback_clears_the_reference(self) -> None:
        sink = _RecordingSink()
        io = AudioIO(far_sink=sink)
        io.play(b"\x01\x02" * 100)
        assert io.stop_playback() == 200
        assert sink.clears == 1

    def test_stop_playback_on_empty_buffer_keeps_the_reference(self) -> None:
        """Nothing was dropped, so nothing became phantom: audio already
        handed to the device is still echoing and still needs its
        reference."""
        sink = _RecordingSink()
        io = AudioIO(far_sink=sink)
        assert io.stop_playback() == 0
        assert sink.clears == 0

    def test_bare_callable_sink_is_accepted(self) -> None:
        pushed: list[bytes] = []
        cleared: list[int] = []
        io = AudioIO(far_sink=pushed.append, far_clear=lambda: cleared.append(1))
        io.play(b"\x07\x08" * 5)
        io.stop_playback()
        assert pushed == [b"\x07\x08" * 5]
        assert cleared == [1]

    def test_a_broken_sink_never_breaks_playback(self) -> None:
        class _Angry:
            def push_far(self, pcm: bytes) -> None:
                raise RuntimeError("no")

            def clear(self) -> None:
                raise RuntimeError("still no")

        io = AudioIO(far_sink=_Angry())
        io.play(b"\x01\x02" * 50)
        assert io.playing is True
        assert io.stop_playback() == 100

    def test_sink_can_be_attached_after_construction(self) -> None:
        sink = _RecordingSink()
        io = AudioIO()
        io.far_sink = sink
        io.play(b"\x01\x02" * 3)
        assert sink.pushed == [b"\x01\x02" * 3]

    def test_volume_does_not_change_the_reference_bytes(self) -> None:
        """Documented: the reference is pre-volume. A scalar gain is what
        an adaptive filter converges on; scaling twice would only cost
        per-sample work on the reply path."""
        sink = _RecordingSink()
        io = AudioIO(far_sink=sink)
        io.output_volume = 0.5
        pcm = _pcm([1000, -1000])
        io.play(pcm)
        assert sink.pushed == [pcm]
