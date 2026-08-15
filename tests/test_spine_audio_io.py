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


def test_user_output_mute_drops_playback() -> None:
    io = AudioIO()
    io.mute_output_user = True
    io.play(b"\x01\x02" * 100)
    assert io.playing is False

    io.mute_output_user = False
    io.play(b"\x01\x02" * 100)
    assert io.playing is True
