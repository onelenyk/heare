"""Microphone in, speaker out, no framework.

Full-duplex local audio with half-duplex gating.
"""

import asyncio
import threading
from typing import Optional


class AudioIO:
    """Microphone in, speaker out, no framework.

    Input: 16 kHz mono int16 delivered as 20 ms frames (640 bytes) into an
    asyncio.Queue from the sounddevice callback thread via
    loop.call_soon_threadsafe. Output: 24 kHz mono int16 PCM played from an
    internal buffer; play() appends, stop_playback() drops everything queued
    (barge-in). mute_input=True makes the input callback discard frames
    (half-duplex while the assistant speaks). Callbacks never block.

    Design constraints:
    - Input callback runs on sounddevice thread; must not block.
    - Output callback runs on sounddevice thread; must not block.
    - asyncio loop is captured at start() and used via call_soon_threadsafe.
    - Input frames are dropped silently if queue is full (no backpressure).
    - Output buffer is protected by a lock; output callback holds it only
      while copying bytes.
    - sounddevice is imported lazily in start() so tests can run without audio.
    """

    def __init__(
        self,
        input_rate: int = 16000,
        output_rate: int = 24000,
        frame_ms: int = 20,
    ) -> None:
        """Initialize AudioIO.

        Args:
            input_rate: Sample rate for input microphone (default 16000 Hz).
            output_rate: Sample rate for output speaker (default 24000 Hz).
            frame_ms: Frame duration in milliseconds (default 20 ms).
        """
        self.input_rate = input_rate
        self.output_rate = output_rate
        self.frame_ms = frame_ms

        # Input frame size in bytes (mono int16 = 2 bytes per sample)
        self.input_frame_bytes = (input_rate * frame_ms // 1000) * 2

        # Output frame size in bytes
        self.output_frame_bytes = (output_rate * frame_ms // 1000) * 2

        # Input queue: holds raw bytes from microphone
        self.input_frames: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)

        # Mute input during assistant playback (half-duplex gating)
        self.mute_input = False

        # Output buffer and lock
        self._output_buffer = bytearray()
        self._output_lock = threading.Lock()

        # sounddevice streams (opened in start())
        self._input_stream: Optional[object] = None
        self._output_stream: Optional[object] = None

        # Event loop where we'll enqueue input frames
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def start(self) -> None:
        """Open sounddevice streams and capture running event loop."""
        import sounddevice

        self._loop = asyncio.get_running_loop()

        self._input_stream = sounddevice.RawInputStream(
            channels=1,
            samplerate=self.input_rate,
            dtype="int16",
            callback=self._on_input,
            blocksize=(self.input_rate * self.frame_ms) // 1000,
        )

        self._output_stream = sounddevice.RawOutputStream(
            channels=1,
            samplerate=self.output_rate,
            dtype="int16",
            callback=self._on_output,
            blocksize=(self.output_rate * self.frame_ms) // 1000,
        )

        self._input_stream.start()
        self._output_stream.start()

    async def stop(self) -> None:
        """Stop and close sounddevice streams."""
        if self._input_stream is not None:
            self._input_stream.stop()
            self._input_stream.close()
            self._input_stream = None

        if self._output_stream is not None:
            self._output_stream.stop()
            self._output_stream.close()
            self._output_stream = None

        self._loop = None

    def _on_input(
        self,
        indata: object,
        frames: int,
        time_info: object,
        status: object,
    ) -> None:
        """Input callback from sounddevice (runs on callback thread).

        If mute_input is True, discard the frame. Otherwise, send the raw
        bytes to input_frames queue via call_soon_threadsafe. If queue is
        full, drop the frame silently (no blocking, no raising).

        Args:
            indata: Buffer with recorded audio (supports bytes() conversion).
            frames: Number of frames in the buffer.
            time_info: Timing information (unused).
            status: Status flags (unused).
        """
        if self.mute_input:
            return

        frame_bytes = bytes(indata)
        loop = self._loop

        if loop is not None:
            # QueueFull is raised later, inside the scheduled call on the
            # event-loop thread — catching it here around the scheduling
            # call would be dead code, so the drop lives in _enqueue.
            loop.call_soon_threadsafe(self._enqueue, frame_bytes)

    def _enqueue(self, frame_bytes: bytes) -> None:
        """Runs on the event-loop thread; drops silently when full."""
        try:
            self.input_frames.put_nowait(frame_bytes)
        except asyncio.QueueFull:
            pass

    def _on_output(
        self,
        outdata: object,
        frames: int,
        time_info: object,
        status: object,
    ) -> None:
        """Output callback from sounddevice (runs on callback thread).

        Pull bytes from the internal buffer, write to outdata, and pad the
        remainder with zeros (silence). The outdata object is a writable
        buffer (memoryview or similar).

        Args:
            outdata: Writable buffer to fill with audio samples.
            frames: Number of frames to fill.
            time_info: Timing information (unused).
            status: Status flags (unused).
        """
        frame_bytes = frames * 2  # mono int16 = 2 bytes per frame

        with self._output_lock:
            if len(self._output_buffer) >= frame_bytes:
                # Buffer has enough; copy and remove from front
                outdata[:] = self._output_buffer[:frame_bytes]
                del self._output_buffer[:frame_bytes]
            elif len(self._output_buffer) > 0:
                # Buffer has some but not enough; copy what we have, pad rest
                available = len(self._output_buffer)
                outdata[:available] = self._output_buffer
                outdata[available:] = b"\x00" * (frame_bytes - available)
                del self._output_buffer[:]
            else:
                # Buffer is empty; fill with silence
                outdata[:] = b"\x00" * frame_bytes

    def play(self, pcm: bytes) -> None:
        """Append PCM audio to the output buffer for playback.

        Non-blocking. Runs on the caller's thread (assumed to be the main
        event loop thread, not the callback thread).

        Args:
            pcm: Raw int16 PCM bytes at output_rate (24 kHz default).
        """
        with self._output_lock:
            self._output_buffer.extend(pcm)

    def stop_playback(self) -> int:
        """Drop all queued audio and return bytes dropped (barge-in).

        This enables the assistant to be interrupted; new input immediately
        starts playback of a new utterance.

        Returns:
            Number of bytes removed from the output buffer.
        """
        with self._output_lock:
            dropped = len(self._output_buffer)
            self._output_buffer.clear()
            return dropped

    @property
    def playing(self) -> bool:
        """True while output buffer is non-empty."""
        with self._output_lock:
            return len(self._output_buffer) > 0
