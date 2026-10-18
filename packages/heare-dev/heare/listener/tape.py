"""A microphone and a speaker that are tape, running at the speed of sound.

The listener's whole point is a closed loop nobody has to sit through:
real speech in, the real recogniser, model and synthesiser in the
middle, and whatever would have left the speaker kept for a second
recogniser to hear. So the device is the only thing replaced, and it
keeps a real device's two habits:

* the microphone never stops. Frames arrive every 20 ms whether or not
  anyone is talking, because the end of a phrase is detected in the
  silence after it;
* the speaker has a clock. Audio handed over sounds *later*, one byte
  after another, so "when was the first sound" and "how long after the
  interruption did it fall silent" are read from that clock and not from
  the moment a buffer was appended.

It has the surface the voice path asks of a device (``input_frames``,
``play``, ``playing``, ``stop_playback``, the mute flags, ``far_sink``),
so the old app and the new one take it unchanged.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

IN_RATE = 16000
OUT_RATE = 24000
FRAME_BYTES = 640  # 20 ms of 16 kHz mono int16
FRAME_S = 0.02
_SILENCE = b"\x00" * FRAME_BYTES


@dataclass
class Sounded:
    """One piece of audio and the instant it started leaving the speaker."""

    at: float
    pcm: bytes

    @property
    def ends(self) -> float:
        return self.at + len(self.pcm) / (OUT_RATE * 2)


class Tape:
    def __init__(self) -> None:
        self.input_frames: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self.mute_input = False
        self.mute_input_user = False
        self.mute_output = False
        self.mute_output_user = False
        self.far_sink = None
        self.sounded: list[Sounded] = []
        # When the last speech frame was put on the microphone: the end
        # of the question, which every latency is measured from.
        self.spoke_until = 0.0
        self.spoke_from = 0.0
        self._quiet_at = 0.0
        self._pending = bytearray()
        self._pump: asyncio.Task | None = None

    # -- the microphone ---------------------------------------------------

    async def start(self) -> None:
        self._pump = asyncio.create_task(self._run(), name="tape-mic")

    async def stop(self) -> None:
        if self._pump is not None:
            self._pump.cancel()
            await asyncio.gather(self._pump, return_exceptions=True)

    def say(self, pcm: bytes) -> None:
        """Put 16 kHz speech on the microphone, starting now."""
        if not self._pending:
            self.spoke_from = time.monotonic()
        self._pending.extend(pcm)

    @property
    def saying(self) -> bool:
        return bool(self._pending)

    async def said(self) -> None:
        """Wait until the speech queued by ``say`` has all been heard."""
        while self._pending:
            await asyncio.sleep(FRAME_S)

    def take_input_gap(self) -> bool:
        return False

    async def _run(self) -> None:
        tick = time.monotonic()
        while True:
            if self._pending:
                frame = bytes(self._pending[:FRAME_BYTES]).ljust(FRAME_BYTES, b"\x00")
                del self._pending[:FRAME_BYTES]
                if not self._pending:
                    self.spoke_until = time.monotonic() + FRAME_S
            else:
                frame = _SILENCE
            if not (self.mute_input or self.mute_input_user):
                try:
                    self.input_frames.put_nowait(frame)
                except asyncio.QueueFull:
                    pass  # what a real device does: drop, never block
            tick += FRAME_S
            await asyncio.sleep(max(0.0, tick - time.monotonic()))

    # -- the speaker ------------------------------------------------------

    def play(self, pcm: bytes) -> None:
        if self.mute_output or self.mute_output_user or not pcm:
            return
        at = max(self._quiet_at, time.monotonic())
        self.sounded.append(Sounded(at, pcm))
        self._quiet_at = at + len(pcm) / (OUT_RATE * 2)
        push = getattr(self.far_sink, "push_far", None)
        if push is not None:
            try:
                push(pcm)
            except Exception:  # noqa: BLE001
                pass

    @property
    def playing(self) -> bool:
        return time.monotonic() < self._quiet_at

    def stop_playback(self) -> int:
        """Drop what has not sounded yet; keep the record honest about it."""
        now = time.monotonic()
        dropped = max(0, int((self._quiet_at - now) * OUT_RATE * 2))
        kept: list[Sounded] = []
        for piece in self.sounded:
            if piece.at >= now:
                continue
            if piece.ends > now:
                n = int((now - piece.at) * OUT_RATE) * 2
                piece = Sounded(piece.at, piece.pcm[:n])
            kept.append(piece)
        self.sounded = kept
        self._quiet_at = min(self._quiet_at, now)
        if dropped:
            clear = getattr(self.far_sink, "clear", None)
            if clear is not None:
                try:
                    clear()
                except Exception:  # noqa: BLE001
                    pass
        return dropped

    # -- what the listener asks -----------------------------------------

    @property
    def quiet_at(self) -> float:
        """When the speaker falls, or fell, silent."""
        return self._quiet_at

    def since(self, mark: float) -> list[Sounded]:
        return [p for p in self.sounded if p.at >= mark]

    async def until_sound(self, mark: float, timeout: float = 30.0) -> float:
        """Wait for the first sound after ``mark``; returns when it starts."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            heard = self.since(mark)
            if heard:
                return heard[0].at
            await asyncio.sleep(0.02)
        raise TimeoutError("nothing was said")

    async def until_quiet(self, settle: float = 1.5, timeout: float = 60.0) -> None:
        """Wait for the reply to end: the speaker silent for ``settle``
        seconds, since sentences arrive with gaps between them."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if time.monotonic() - self._quiet_at >= settle:
                return
            await asyncio.sleep(0.05)
        raise TimeoutError("the speaker never fell silent")
