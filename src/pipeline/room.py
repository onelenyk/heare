"""A simulated room, so the acoustic half can be tested without one.

Everything about echo and interruption used to require a person, a pair
of speakers and a quiet afternoon — which is why four separate bugs in
the echo path survived for months, and why "it does not hear itself" was
believed when the truth was "it is deaf while speaking".

Here the microphone is arithmetic:

    mic = scripted speech
        + echo_gain × (what the daemon played, delayed)
        + a noise floor

Nothing is opened. The transport is built with its devices disabled and
frames are fed into the pipeline in real time — one 20 ms frame every
20 ms, because feeding faster would hand the VAD half a minute of speech
in a second and every timing would become fiction.

    uv run python -m src.pipeline.room                 # the default scenario
    uv run python -m src.pipeline.room --echo -6       # a louder room

What it can prove: whether the user can interrupt, whether the assistant
answers its own voice, how fast TTS stops, whether AEC converges at a
given delay. What it cannot: reverberation, speaker distortion, the
microphone's own gain control. A passing simulation does not promise the
hardware works — a failing one promises it does not.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger("room")

SAMPLE_RATE = 16_000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
MID_SPEECH = "mid_speech"


# ── the script ────────────────────────────────────────────────────────


@dataclass
class Say:
    """One scripted utterance.

    ``at`` is seconds from the start, or ``"mid_speech"`` — wait until
    the assistant is speaking and cut in. That second form is the whole
    reason for this module: interrupting at a repeatable moment is
    something a person in a room cannot do twice the same way.
    """

    at: float | str
    text: str
    # 0.4 s, not 1.5: the assistant's own reply rules cap most answers at
    # one sentence, so by 1.5 s there is usually nothing left to interrupt.
    delay_after_bot_starts: float = 0.4


@dataclass
class Event:
    at: float
    kind: str
    detail: str = ""


@dataclass
class RoomResult:
    events: list[Event] = field(default_factory=list)
    heard: list[str] = field(default_factory=list)
    said: list[str] = field(default_factory=list)
    heard_itself: int = 0
    barge_in_ms: float | None = None
    bot_utterances: int = 0
    # Counters, because "nothing was heard" has three different causes
    # and they are indistinguishable from the outcome alone: frames never
    # fed, VAD never fired, or STT never returned.
    frames_fed: int = 0
    frames_seen_after_stt: int = 0
    vad_starts: int = 0

    def add(self, kind: str, detail: str, started: float) -> None:
        self.events.append(Event((time.monotonic() - started) * 1000, kind, detail))


# ── speech ────────────────────────────────────────────────────────────


async def synthesize(text: str, voice: str = "uk-UA-PolinaNeural") -> np.ndarray:
    """Text to 16 kHz mono PCM, via the edge-tts already in the project.

    A different voice from the assistant's on purpose: identical voices
    would make "did it hear itself" unanswerable.
    """
    import edge_tts

    mp3 = bytearray()
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            mp3.extend(chunk["data"])
    if not mp3:
        return np.zeros(0, dtype=np.int16)

    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is required to decode synthesized speech")

    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "pipe:1"],
        input=bytes(mp3),
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(out, dtype=np.int16).copy()


def _overlap(a: str, b: str) -> float:
    """Token overlap, for deciding whether a transcript is our own echo."""
    ta = {w.strip(".,!?—«»").lower() for w in a.split() if w.strip(".,!?—«»")}
    tb = {w.strip(".,!?—«»").lower() for w in b.split() if w.strip(".,!?—«»")}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


# ── the room ──────────────────────────────────────────────────────────


class Room:
    """Mixes scripted speech with the daemon's own delayed output."""

    def __init__(
        self,
        *,
        echo_db: float = -10.0,
        delay_ms: int = 120,
        noise_dbfs: float = -60.0,
    ) -> None:
        self.echo_gain = 10 ** (echo_db / 20)
        self.delay_samples = int(delay_ms * SAMPLE_RATE / 1000)
        self.noise_amp = 32768 * 10 ** (noise_dbfs / 20)
        self.echo_db = echo_db
        self.delay_ms = delay_ms
        self.noise_dbfs = noise_dbfs

        # What the daemon played, at mic rate, oldest first.
        self._played = np.zeros(0, dtype=np.float32)
        self._read_pos = 0
        self._rng = np.random.default_rng(20260810)

        self.bot_speaking = False
        self.last_bot_text = ""
        # BotStartedSpeakingFrame is emitted by the output transport, so a
        # stage sitting before it never sees one. Audio leaving TTS is the
        # honest signal that sound is on its way out.
        self.last_audio_at = 0.0

    # -- what the daemon plays ----------------------------------------

    def played(self, pcm: bytes, source_rate: int) -> None:
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        if samples.size == 0:
            return
        if source_rate != SAMPLE_RATE:
            new_len = max(1, int(samples.size * SAMPLE_RATE / source_rate))
            samples = np.interp(
                np.linspace(0, samples.size - 1, new_len),
                np.arange(samples.size),
                samples,
            )
        self._played = np.concatenate([self._played, samples])

    def _echo(self, n: int) -> np.ndarray:
        """The next ``n`` samples of echo, delayed by the room."""
        start = self._read_pos - self.delay_samples
        self._read_pos += n
        if start < 0:
            return np.zeros(n, dtype=np.float32)
        available = self._played[start : start + n]
        if available.size < n:
            available = np.concatenate(
                [available, np.zeros(n - available.size, dtype=np.float32)]
            )
        return available * self.echo_gain

    def mic_frame(self, speech: np.ndarray) -> bytes:
        """One 20 ms microphone frame: speech + echo + noise."""
        frame = speech.astype(np.float32)
        if frame.size < FRAME_SAMPLES:
            frame = np.concatenate(
                [frame, np.zeros(FRAME_SAMPLES - frame.size, dtype=np.float32)]
            )
        frame = frame + self._echo(FRAME_SAMPLES)
        frame = frame + self._rng.normal(0, self.noise_amp, FRAME_SAMPLES)
        return np.clip(frame, -32768, 32767).astype(np.int16).tobytes()

    # -- running a scenario -------------------------------------------

    async def run(
        self, script: list[Say], *, timeout: float = 40.0
    ) -> RoomResult:
        from pipecat.frames.frames import (
            EndFrame,
            InputAudioRawFrame,
            InterruptionFrame,
            TranscriptionFrame,
            TTSAudioRawFrame,
            TTSTextFrame,
            UserStartedSpeakingFrame,
        )
        from pipecat.frames.frames import StartFrame
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

        from src.pipeline.harness import _build_daemon

        result = RoomResult()
        speech_q: asyncio.Queue = asyncio.Queue()
        started = time.monotonic()
        room = self
        interrupt_pending: dict = {}

        class Ears(FrameProcessor):
            """Watches what the daemon heard and when it spoke."""

            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                if isinstance(frame, InputAudioRawFrame):
                    result.frames_seen_after_stt += 1
                elif isinstance(frame, UserStartedSpeakingFrame):
                    result.vad_starts += 1
                if isinstance(frame, TranscriptionFrame):
                    text = frame.text or ""
                    result.heard.append(text)
                    echo_like = _overlap(text, room.last_bot_text) > 0.5
                    if echo_like and room.bot_speaking:
                        result.heard_itself += 1
                        result.add("heard_itself", text, started)
                    else:
                        result.add("heard", text, started)
                elif isinstance(frame, InterruptionFrame):
                    at = interrupt_pending.pop("at", None)
                    if at is not None:
                        result.barge_in_ms = (time.monotonic() - at) * 1000
                    result.add("interrupt", "", started)
                await self.push_frame(frame, direction)

        class Mouth(FrameProcessor):
            """Captures what the daemon plays, so the room can echo it."""

            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                if isinstance(frame, TTSAudioRawFrame):
                    room.played(frame.audio, getattr(frame, "sample_rate", 24000))
                    room.last_audio_at = time.monotonic()
                    if not room.bot_speaking:
                        room.bot_speaking = True
                        result.bot_utterances += 1
                        result.add("bot_started", "", started)
                elif isinstance(frame, TTSTextFrame):
                    room.last_bot_text = frame.text or ""
                await self.push_frame(frame, direction)

        class Microphone(FrameProcessor):
            """Sits where the input device would, and speaks into the chain.

            Frames queued at the task source never arrived: they pass
            through transport.input(), which is switched off here, and a
            disabled input transport drops them. Generating from inside
            the chain also puts them exactly where a real microphone's
            frames appear, so every downstream stage sees what it would
            see in the room.
            """

            def __init__(self) -> None:
                super().__init__()
                self._pump: asyncio.Task | None = None

            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                await self.push_frame(frame, direction)
                if self._pump is None and isinstance(frame, StartFrame):
                    self._pump = asyncio.create_task(self._run())

            async def _run(self) -> None:
                next_frame = time.monotonic()
                while True:
                    try:
                        chunk = speech_q.get_nowait()
                    except asyncio.QueueEmpty:
                        chunk = np.zeros(FRAME_SAMPLES, dtype=np.int16)
                    await self.push_frame(
                        InputAudioRawFrame(
                            audio=room.mic_frame(chunk),
                            sample_rate=SAMPLE_RATE,
                            num_channels=1,
                        ),
                        FrameDirection.DOWNSTREAM,
                    )
                    result.frames_fed += 1
                    next_frame += FRAME_MS / 1000
                    delay = next_frame - time.monotonic()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    else:
                        next_frame = time.monotonic()

        task, memory_backend = await _build_daemon(
            audio_probes=[Microphone()],
            post_stt_stages=[Ears()],
            pre_output_stages=[Mouth()],
        )

        runner = PipelineRunner(handle_sigint=False)
        runner_task = asyncio.create_task(runner.run(task))
        await asyncio.sleep(0.5)

        # Synthesize everything up front: generating mid-scenario would
        # stall the frame clock and the timings would be about the network.
        clips = [(line, await synthesize(line.text)) for line in script]

        pending = list(clips)
        active: np.ndarray | None = None
        active_pos = 0
        bot_started_at: float | None = None
        deadline = time.monotonic() + timeout
        quiet_since: float | None = None

        while time.monotonic() < deadline:
            now = time.monotonic() - started

            if (
                room.bot_speaking
                and time.monotonic() - room.last_audio_at > 0.6
            ):
                room.bot_speaking = False
                result.add("bot_stopped", "", started)

            if room.bot_speaking and bot_started_at is None:
                bot_started_at = now

            if active is None and pending:
                line, clip = pending[0]
                if isinstance(line.at, (int, float)):
                    due = line.at
                elif bot_started_at is not None:
                    due = bot_started_at + line.delay_after_bot_starts
                else:
                    due = None
                if due is not None and now >= due:
                    pending.pop(0)
                    active, active_pos = clip, 0
                    result.said.append(line.text)
                    result.add("said", line.text, started)
                    if room.bot_speaking:
                        interrupt_pending["at"] = time.monotonic()

            if active is not None:
                chunk = active[active_pos : active_pos + FRAME_SAMPLES]
                active_pos += FRAME_SAMPLES
                if active_pos >= active.size:
                    active = None
                speech_q.put_nowait(chunk)

            # Stop once everything has been said, the assistant has
            # answered, and the room has been quiet for a while.
            done = not pending and active is None and not room.bot_speaking
            if done and result.heard and result.bot_utterances:
                quiet_since = quiet_since or time.monotonic()
                if time.monotonic() - quiet_since > 3.0:
                    break
            else:
                quiet_since = None

            await asyncio.sleep(FRAME_MS / 1000)

        await task.queue_frames([EndFrame()])
        try:
            await asyncio.wait_for(runner_task, timeout=10)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            runner_task.cancel()
        await memory_backend.close()
        return result


# ── reporting ─────────────────────────────────────────────────────────

_GLYPH = {
    "said": "▶ сказано   ",
    "heard": "◀ почуто    ",
    "heard_itself": "‼ почув себе",
    "bot_started": "♪ бот       ",
    "bot_stopped": "♪ бот       ",
    "interrupt": "⚡ переривання",
}


def report(room: Room, result: RoomResult) -> int:
    print(
        f"\n── room: echo {room.echo_db:g} dB, delay {room.delay_ms} ms, "
        f"noise {room.noise_dbfs:g} dBFS ──"
    )
    for e in result.events:
        label = _GLYPH.get(e.kind, e.kind)
        detail = e.detail
        if e.kind == "bot_started":
            detail = "заговорив"
        elif e.kind == "bot_stopped":
            detail = "замовк"
        print(f"  {e.at / 1000:6.2f} s  {label}  {detail[:70]}")

    print("─" * 66)
    print(f"  почув себе          {result.heard_itself}")
    barge = f"{result.barge_in_ms:.0f} ms" if result.barge_in_ms else "не спрацювало"
    print(f"  перебивання         {barge}")
    print(f"  транскрипцій        {len(result.heard)}")
    print(f"  реплік бота         {result.bot_utterances}")
    print(
        f"  кадрів: подано {result.frames_fed}, "
        f"пройшло STT {result.frames_seen_after_stt}, "
        f"VAD спрацював {result.vad_starts}×"
    )

    ok = result.heard_itself == 0 and bool(result.heard)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--echo", type=float, default=-10.0, help="echo level in dB")
    p.add_argument("--delay-ms", type=int, default=120)
    p.add_argument("--noise", type=float, default=-60.0)
    p.add_argument("--window", type=float, default=40.0)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    room = Room(echo_db=args.echo, delay_ms=args.delay_ms, noise_dbfs=args.noise)
    script = [
        # Explicitly long, because the reply rules otherwise cap answers
        # at a single sentence and leave nothing to interrupt.
        Say(
            at=0.0,
            text=(
                "Перелічи будь ласка всі вісім планет сонячної системи "
                "по черзі, повними реченнями, не поспішаючи."
            ),
        ),
        Say(at=MID_SPEECH, text="Стоп, зачекай."),
    ]
    result = asyncio.run(room.run(script, timeout=args.window))
    return report(room, result)


if __name__ == "__main__":
    sys.exit(main())
