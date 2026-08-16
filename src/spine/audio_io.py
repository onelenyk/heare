"""Microphone in, speaker out, no framework.

Full-duplex local audio with half-duplex gating.
"""

import array
import asyncio
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_INT16_MIN = -32768
_INT16_MAX = 32767


def _scale_int16(data: bytes, factor: float) -> bytes:
    """Multiply little-endian int16 PCM by `factor`, clamped to int16.

    Clamping is the whole point: an unclamped multiply wraps +32800 to a
    large negative sample, which is heard as a crack and, historically,
    was what destroyed the echo canceller (docs/findings/echo-cancellation.md).
    stdlib only — this file must import without numpy.
    """
    usable = len(data) - (len(data) % 2)
    samples = array.array("h")
    samples.frombytes(data[:usable])
    for i, s in enumerate(samples):
        scaled = int(s * factor)
        if scaled > _INT16_MAX:
            scaled = _INT16_MAX
        elif scaled < _INT16_MIN:
            scaled = _INT16_MIN
        samples[i] = scaled
    out = samples.tobytes()
    # A stray odd byte can only come from a malformed frame; pass it
    # through untouched rather than silently shortening the frame.
    return out + data[usable:] if usable != len(data) else out


class AudioIO:
    """Microphone in, speaker out, no framework.

    Input: 16 kHz mono int16 delivered as 20 ms frames (640 bytes) into an
    asyncio.Queue from the sounddevice callback thread via
    loop.call_soon_threadsafe. Output: 24 kHz mono int16 PCM played from an
    internal buffer; play() appends, stop_playback() drops everything queued
    (barge-in). mute_input=True makes the input callback discard frames
    (half-duplex while the assistant speaks) and records a gap for the
    segmenter downstream. Callbacks never block. An optional far_sink
    receives the echo canceller's reference from inside play()/stop_playback(),
    which are the only two places that know what the speaker really got.

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
        input_device: object = None,
        output_device: object = None,
        far_sink: object = None,
        far_clear: object = None,
    ) -> None:
        """Initialize AudioIO.

        Args:
            input_rate: Sample rate for input microphone (default 16000 Hz).
            output_rate: Sample rate for output speaker (default 24000 Hz).
            frame_ms: Frame duration in milliseconds (default 20 ms).
            input_device: sounddevice device index or name for the mic;
                None means the system default.
            output_device: sounddevice device index or name for the
                speaker; None means the system default.
            far_sink: optional echo-canceller handle (anything with
                `push_far(pcm)` and `clear()`, e.g. `SpineAEC`) or a bare
                callable taking the PCM bytes. None keeps the old
                behaviour, where whoever calls play() also feeds the
                canceller.
            far_clear: optional `clear()` companion when `far_sink` is a
                bare callable; ignored when the sink has its own.
        """
        self.input_rate = input_rate
        self.output_rate = output_rate
        self.frame_ms = frame_ms
        self.input_device = input_device
        self.output_device = output_device

        # Input frame size in bytes (mono int16 = 2 bytes per sample)
        self.input_frame_bytes = (input_rate * frame_ms // 1000) * 2

        # Output frame size in bytes
        self.output_frame_bytes = (output_rate * frame_ms // 1000) * 2

        # Input queue: holds raw bytes from microphone
        self.input_frames: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)

        # Mute input during assistant playback (half-duplex gating).
        # Properties, not plain attributes: muting tears a hole in the
        # frame stream, and the segmenter downstream has to be told (see
        # note_input_gap).
        self._mute_input = False
        self._mute_input_user = False
        self._input_gap = False
        self.mute_output = False
        # The user's own switches (dashboard/menubar), kept apart from the
        # two above: the conductor toggles mute_input every half-duplex
        # reply and a quiet role owns mute_output, so sharing one flag
        # would silently undo whatever the user chose.
        self.mute_output_user = False

        # Far-end reference for the echo canceller. Owned here because
        # this class is the only place that knows whether bytes actually
        # reached the speaker: play() drops them while muted, and
        # stop_playback() drops what was queued. A reference filled by the
        # caller before those two checks describes audio no speaker ever
        # emitted, and AEC3 then subtracts a phantom echo from the user's
        # own voice.
        self.far_sink = far_sink
        self.far_clear = far_clear

        # The user's two sliders. 1.0 is the identity and is a fast path:
        # untouched bytes, no per-sample arithmetic in the callback.
        self.input_gain = 1.0
        self.output_volume = 1.0

        # Output buffer and lock
        self._output_buffer = bytearray()
        self._output_lock = threading.Lock()

        # sounddevice streams (opened in start())
        self._input_stream: Optional[object] = None
        self._output_stream: Optional[object] = None

        # Event loop where we'll enqueue input frames
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # -- input mute and the hole it leaves ----------------------------

    @property
    def mute_input(self) -> bool:
        """Half-duplex gate owned by the conductor."""
        return self._mute_input

    @mute_input.setter
    def mute_input(self, value: bool) -> None:
        if value and not self._mute_input:
            self.note_input_gap()
        self._mute_input = value

    @property
    def mute_input_user(self) -> bool:
        """The user's own mic switch (dashboard/menubar)."""
        return self._mute_input_user

    @mute_input_user.setter
    def mute_input_user(self, value: bool) -> None:
        if value and not self._mute_input_user:
            self.note_input_gap()
        self._mute_input_user = value

    def note_input_gap(self) -> None:
        """Announce that the frame stream is about to have a hole.

        Recorded on every mute edge, and callable directly by anything
        else that interrupts capture (a device switch, a restarted
        stream). The flag is sticky until `take_input_gap()` consumes it,
        so the first frame after unmute carries the news no matter how
        long the mute lasted.
        """
        self._input_gap = True

    def take_input_gap(self) -> bool:
        """True once per hole, then False; clears the flag.

        The frame consumer calls this before feeding the VAD and, when it
        is True, drops the utterance in flight — otherwise the sentence
        interrupted by the mute is glued to the next one and transcribed
        as a whole.
        """
        gap = self._input_gap
        self._input_gap = False
        return gap

    # -- far-end reference --------------------------------------------

    def _far_push(self, pcm: bytes) -> None:
        """Hand the canceller bytes that really are going to the speaker."""
        sink = self.far_sink
        if sink is None or not pcm:
            return
        try:
            push = getattr(sink, "push_far", None)
            if push is None:
                sink(pcm)  # type: ignore[operator]
            else:
                push(pcm)
        except Exception:
            # The reply must still be heard if the canceller misbehaves.
            logger.debug("far-end push failed (non-fatal)", exc_info=True)

    def _far_clear(self) -> None:
        """Forget queued reference for audio that will never be played."""
        sink = self.far_sink
        clear = getattr(sink, "clear", None) if sink is not None else None
        if clear is None:
            clear = self.far_clear
        if clear is None:
            return
        try:
            clear()  # type: ignore[operator]
        except Exception:
            logger.debug("far-end clear failed (non-fatal)", exc_info=True)

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
            device=self.input_device,
        )

        self._output_stream = sounddevice.RawOutputStream(
            channels=1,
            samplerate=self.output_rate,
            dtype="int16",
            callback=self._on_output,
            blocksize=(self.output_rate * self.frame_ms) // 1000,
            device=self.output_device,
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

        If mute_input is True, discard the frame. Otherwise, apply
        input_gain (a no-op at 1.0) and send the bytes to the
        input_frames queue via call_soon_threadsafe. If queue is full,
        drop the frame silently (no blocking, no raising).

        Args:
            indata: Buffer with recorded audio (supports bytes() conversion).
            frames: Number of frames in the buffer.
            time_info: Timing information (unused).
            status: Status flags (unused).
        """
        if self.mute_input or self.mute_input_user:
            return

        frame_bytes = bytes(indata)
        gain = self.input_gain
        if gain != 1.0:
            frame_bytes = _scale_int16(frame_bytes, gain)
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

        Pull bytes from the internal buffer, scale them by output_volume
        (a no-op at 1.0), write to outdata, and pad the remainder with
        zeros (silence). The outdata object is a writable buffer
        (memoryview or similar).

        Args:
            outdata: Writable buffer to fill with audio samples.
            frames: Number of frames to fill.
            time_info: Timing information (unused).
            status: Status flags (unused).
        """
        frame_bytes = frames * 2  # mono int16 = 2 bytes per frame
        volume = self.output_volume

        with self._output_lock:
            if len(self._output_buffer) >= frame_bytes:
                if volume == 1.0:
                    # Fast path: straight copy, no per-sample work.
                    outdata[:] = self._output_buffer[:frame_bytes]
                    del self._output_buffer[:frame_bytes]
                    return
                chunk = bytes(self._output_buffer[:frame_bytes])
                del self._output_buffer[:frame_bytes]
            elif len(self._output_buffer) > 0:
                chunk = bytes(self._output_buffer)
                del self._output_buffer[:]
            else:
                # Buffer is empty; fill with silence
                outdata[:] = b"\x00" * frame_bytes
                return

        # Scaling happens outside the lock: play() must not wait on it.
        if volume != 1.0:
            chunk = _scale_int16(chunk, volume)

        if len(chunk) >= frame_bytes:
            outdata[:] = chunk[:frame_bytes]
        else:
            outdata[: len(chunk)] = chunk
            outdata[len(chunk):] = b"\x00" * (frame_bytes - len(chunk))

    def play(self, pcm: bytes) -> None:
        """Append PCM audio to the output buffer for playback.

        Non-blocking. Runs on the caller's thread (assumed to be the main
        event loop thread, not the callback thread).

        When mute_output is True the bytes are dropped here, before the
        buffer: quiet roles (a meeting secretary) promise the user the
        assistant cannot sound, and that promise must hold even if some
        caller forgets to check the policy — a hardware-level guarantee,
        like mute_input on the other side.

        The far-end reference is fed from here, after that mute check, so
        the canceller only ever hears about audio the speaker is actually
        going to emit. (`output_volume` still scales the bytes later, in
        the output callback; a scalar gain is what an adaptive filter
        converges on by itself, whereas silence is an absence it cannot.)

        Args:
            pcm: Raw int16 PCM bytes at output_rate (24 kHz default).
        """
        if self.mute_output or self.mute_output_user:
            return
        with self._output_lock:
            self._output_buffer.extend(pcm)
        # Outside the lock: the sink resamples, and the output callback
        # must never wait behind that.
        self._far_push(pcm)

    def stop_playback(self) -> int:
        """Drop all queued audio and return bytes dropped (barge-in).

        This enables the assistant to be interrupted; new input immediately
        starts playback of a new utterance.

        Dropped bytes are already in the far-end reference, so the sink is
        cleared here too — otherwise the canceller spends the next seconds
        subtracting an echo of audio the speaker never emitted, from
        exactly the utterance the user interrupted with. Only when
        something was actually dropped: clearing on an empty buffer would
        throw away reference for audio already on its way through the
        device and still echoing in the room.

        Returns:
            Number of bytes removed from the output buffer.
        """
        with self._output_lock:
            dropped = len(self._output_buffer)
            self._output_buffer.clear()
        if dropped:
            self._far_clear()
        return dropped

    @property
    def playing(self) -> bool:
        """True while output buffer is non-empty."""
        with self._output_lock:
            return len(self._output_buffer) > 0
