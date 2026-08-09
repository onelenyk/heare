"""Does an open microphone survive without pipecat?

The hypothesis under test is not "can we call Groq without a framework" —
HTTP is not in doubt. It is the audio front end:

    On built-in speakers, with no headphones, can we hold a turn without
    hearing ourselves, and be interrupted mid-sentence, using only
    pywebrtc-audio's AudioProcessor?

If yes, silero/onnxruntime (94 MB) and four pipeline stages collapse into
one `process()` call per 10 ms frame, and the rest of the spine is HTTP
calls and `if` statements. If no, we learned it for 300 lines instead of a
rewrite.

Deliberately absent: any LLM, any tools, any context. It repeats what you
say back to you. That is enough to measure the only three numbers that
matter.

    uv run python experiments/spine/echo_probe.py --check   # no mic opened
    uv run python experiments/spine/echo_probe.py           # AEC on
    uv run python experiments/spine/echo_probe.py --no-aec  # control

Run the control. A pass with AEC means nothing unless the same setup fails
without it — otherwise you have only proven your microphone is quiet.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import shutil
import sys
import threading
import time
import wave
from collections import deque
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16_000
FRAME_MS = 10
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 160

# Endpointing. Speech starts fast so barge-in is responsive, and ends slow
# so a pause mid-sentence is not mistaken for the end of a turn.
SPEECH_PROB_ON = 0.65
SPEECH_PROB_OFF = 0.35
FRAMES_TO_START = 3  # 30 ms
FRAMES_TO_END = 60  # 600 ms
MIN_SEGMENT_FRAMES = 20  # 200 ms — below this it is a cough

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3"
VOICE = "uk-UA-OstapNeural"

STOP_WORDS = {"стоп", "stop", "відміна", "відміни", "замовкни", "тихо"}


# ── playback ──────────────────────────────────────────────────────────────
class Playback:
    """Speaker-side buffer, drained by the audio callback 160 samples at a
    time. Doubles as the AEC reference: whatever we hand the speaker is
    exactly what the echo canceller is told to subtract.

    `cancel()` drops everything still queued — this is what barge-in costs,
    and it is why stopping is fast: no fade, no drain, just an empty deque.
    """

    def __init__(self) -> None:
        self._chunks: deque[np.ndarray] = deque()
        self._lock = threading.Lock()
        self.started_at: float | None = None
        self.text: str = ""

    def enqueue(self, pcm: np.ndarray, text: str) -> None:
        with self._lock:
            self._chunks.clear()
            for i in range(0, len(pcm), FRAME_SAMPLES):
                frame = pcm[i : i + FRAME_SAMPLES]
                if len(frame) < FRAME_SAMPLES:
                    frame = np.pad(frame, (0, FRAME_SAMPLES - len(frame)))
                self._chunks.append(frame)
            self.started_at = None
            self.text = text

    def next_frame(self) -> tuple[np.ndarray, bool]:
        """Return (frame, was_playing). Silence when the queue is empty."""
        with self._lock:
            if not self._chunks:
                return np.zeros(FRAME_SAMPLES, dtype=np.int16), False
            if self.started_at is None:
                self.started_at = time.monotonic()
            return self._chunks.popleft(), True

    def cancel(self) -> bool:
        with self._lock:
            had = bool(self._chunks)
            self._chunks.clear()
            return had

    @property
    def busy(self) -> bool:
        with self._lock:
            return bool(self._chunks)


# ── audio front end ───────────────────────────────────────────────────────
class FrontEnd:
    """Mic → AudioProcessor → (clean frame, speech probability).

    One `process()` call does echo cancellation, noise suppression, AGC and
    the high-pass filter, and leaves the VAD estimate in
    `speech_probability`. That single call is what replaces silero, the
    separate AEC stage, and the input-gain stage.
    """

    def __init__(self, playback: Playback, *, aec: bool, delay_ms: int) -> None:
        from pywebrtc_audio import AudioProcessor

        self.apm = AudioProcessor(
            sample_rate=SAMPLE_RATE,
            num_channels=1,
            echo_cancellation=aec,
            noise_suppression=True,
            high_pass_filter=True,
            auto_gain_control=True,
            ns_level=2,
            stream_delay_ms=delay_ms,
        )
        self.aec = aec
        self.playback = playback
        self.out_q: asyncio.Queue | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.dropped = 0

    def callback(self, indata, outdata, frames, time_info, status) -> None:  # noqa: ANN001
        far, playing = self.playback.next_frame()
        outdata[:, 0] = far

        near = np.ascontiguousarray(indata[:, 0])
        # far is passed even while silent: AEC3 keeps its delay estimate
        # converged instead of relearning at the start of every utterance.
        clean = self.apm.process(near, far if self.aec else None)
        prob = self.apm.speech_probability

        if self.loop is not None and self.out_q is not None:
            try:
                self.loop.call_soon_threadsafe(
                    self.out_q.put_nowait, (clean.copy(), prob, playing)
                )
            except (RuntimeError, asyncio.QueueFull):
                self.dropped += 1


# ── network ───────────────────────────────────────────────────────────────
def to_wav(frames: list[np.ndarray]) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(np.concatenate(frames).astype(np.int16).tobytes())
    return buf.getvalue()


async def transcribe(client, api_key: str, wav: bytes) -> str:
    resp = await client.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": ("a.wav", wav, "audio/wav")},
        data={"model": GROQ_MODEL, "temperature": "0"},
        timeout=20.0,
    )
    resp.raise_for_status()
    return (resp.json().get("text") or "").strip()


async def synthesize(text: str) -> np.ndarray:
    """edge-tts → mp3 → ffmpeg → int16 PCM at 16 kHz.

    Asking ffmpeg for 16 kHz directly means no resampler anywhere in this
    file — one of the things the current pipeline pulls 130 MB to do.
    """
    import edge_tts

    mp3 = bytearray()
    async for chunk in edge_tts.Communicate(text, VOICE).stream():
        if chunk["type"] == "audio":
            mp3.extend(chunk["data"])
    if not mp3:
        return np.zeros(0, dtype=np.int16)

    proc = await asyncio.create_subprocess_exec(
        shutil.which("ffmpeg") or "ffmpeg",
        "-hide_banner", "-loglevel", "error",
        "-f", "mp3", "-i", "pipe:0",
        "-f", "s16le", "-acodec", "pcm_s16le",
        "-ar", str(SAMPLE_RATE), "-ac", "1", "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(bytes(mp3))
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg: {err.decode(errors='replace')[:200]}")
    return np.frombuffer(out, dtype=np.int16)


# ── scoring ───────────────────────────────────────────────────────────────
def overlap(a: str, b: str) -> float:
    """Token overlap, used only to decide whether a transcript is our own
    voice coming back. Crude on purpose — a real echo scores near 1.0 and
    an unrelated sentence near 0.0, so the threshold is not delicate.
    """
    ta = {w.strip(".,!?…").lower() for w in a.split() if w.strip(".,!?…")}
    tb = {w.strip(".,!?…").lower() for w in b.split() if w.strip(".,!?…")}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


# ── main loop ─────────────────────────────────────────────────────────────
async def run(args) -> int:  # noqa: ANN001
    import httpx
    import sounddevice as sd

    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        env = Path.home() / ".heare" / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("GROQ_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
    if not api_key:
        print("no GROQ_API_KEY (env or ~/.heare/.env)", file=sys.stderr)
        return 2

    journal = Path(args.journal)
    journal.parent.mkdir(parents=True, exist_ok=True)
    jf = journal.open("a")

    def record(kind: str, **fields) -> None:
        jf.write(json.dumps({"t": round(time.time(), 3), "kind": kind, **fields}) + "\n")
        jf.flush()

    playback = Playback()
    front = FrontEnd(playback, aec=not args.no_aec, delay_ms=args.delay_ms)
    front.loop = asyncio.get_running_loop()
    front.out_q = asyncio.Queue(maxsize=400)

    stats = {"segments": 0, "self_heard": 0, "bargeins": [], "roundtrips": []}
    last_spoken = ""

    stream = sd.Stream(
        samplerate=SAMPLE_RATE,
        blocksize=FRAME_SAMPLES,
        dtype="int16",
        channels=1,
        callback=front.callback,
    )

    print(f"AEC={'on' if front.aec else 'OFF (control)'}  delay={args.delay_ms}ms")
    print("speak — it repeats you back. ctrl-c to stop and print the verdict.\n")
    record("start", aec=front.aec, delay_ms=args.delay_ms)

    speech_run = 0
    silence_run = 0
    capturing = False
    segment: list[np.ndarray] = []
    seg_started_during_playback = False
    speech_onset_at = 0.0

    async with httpx.AsyncClient() as client:
        with stream:
            try:
                while True:
                    frame, prob, playing = await front.out_q.get()

                    if prob >= SPEECH_PROB_ON:
                        speech_run += 1
                        silence_run = 0
                    elif prob <= SPEECH_PROB_OFF:
                        silence_run += 1
                        speech_run = 0

                    if not capturing and speech_run >= FRAMES_TO_START:
                        capturing = True
                        segment = []
                        seg_started_during_playback = playing
                        speech_onset_at = time.monotonic()
                        # Barge-in: stop talking the moment the user does.
                        # No echo check here on purpose — if AEC works, our
                        # own voice never reaches this branch. If it does
                        # not, we want to see it cut itself off.
                        if playback.cancel():
                            lat = (time.monotonic() - speech_onset_at) * 1000
                            stats["bargeins"].append(lat)
                            record("bargein", stop_ms=round(lat, 1))
                            print(f"  [barge-in — stopped in {lat:.0f} ms]")

                    if capturing:
                        segment.append(frame)
                        if silence_run >= FRAMES_TO_END:
                            capturing = False
                            eos = time.monotonic()
                            if len(segment) < MIN_SEGMENT_FRAMES:
                                segment = []
                                continue

                            text = await transcribe(client, api_key, to_wav(segment))
                            segment = []
                            if not text:
                                continue

                            stats["segments"] += 1
                            echo_score = overlap(text, last_spoken)
                            is_self = seg_started_during_playback and echo_score > 0.5
                            record(
                                "heard",
                                text=text,
                                during_playback=seg_started_during_playback,
                                echo_score=round(echo_score, 2),
                                self_heard=is_self,
                            )

                            if is_self:
                                stats["self_heard"] += 1
                                print(f"  !! HEARD ITSELF: {text!r}")
                                continue

                            print(f"  you: {text}")
                            if text.strip().lower().strip(".,!?") in STOP_WORDS:
                                playback.cancel()
                                continue

                            pcm = await synthesize(text)
                            playback.enqueue(pcm, text)
                            last_spoken = text
                            # Wait for the callback to actually pull the
                            # first frame — anything earlier measures our
                            # own bookkeeping, not audible latency.
                            while playback.started_at is None and playback.busy:
                                await asyncio.sleep(0.002)
                            if playback.started_at is not None:
                                rt = (playback.started_at - eos) * 1000
                                stats["roundtrips"].append(rt)
                                record("spoke", text=text, roundtrip_ms=round(rt, 1))
                                print(f"  said back in {rt:.0f} ms")
            except KeyboardInterrupt:
                pass

    jf.close()
    return verdict(stats, front)


def verdict(stats: dict, front: FrontEnd) -> int:
    def stat(xs: list[float]) -> str:
        if not xs:
            return "n/a"
        s = sorted(xs)
        return f"median {s[len(s) // 2]:.0f} ms  worst {s[-1]:.0f} ms  (n={len(s)})"

    print("\n" + "─" * 58)
    print(f"AEC:                {'on' if front.aec else 'OFF (control)'}")
    print(f"segments heard:     {stats['segments']}")
    print(f"heard itself:       {stats['self_heard']}   <- must be 0 with AEC on")
    print(f"barge-in stop:      {stat(stats['bargeins'])}   <- want < 200 ms")
    print(f"speech end → audio: {stat(stats['roundtrips'])}")
    if front.dropped:
        print(f"dropped frames:     {front.dropped}  (consumer too slow)")
    print("─" * 58)

    if not front.aec:
        print("control run — self-hearing here is the expected result.")
        return 0
    ok = stats["self_heard"] == 0 and stats["segments"] > 0
    print("HYPOTHESIS HOLDS" if ok else "HYPOTHESIS FAILS")
    return 0 if ok else 1


def check() -> int:
    """Everything the real run needs, without opening the microphone."""
    ok = True
    try:
        from pywebrtc_audio import AudioProcessor

        apm = AudioProcessor(
            sample_rate=SAMPLE_RATE, num_channels=1,
            echo_cancellation=True, noise_suppression=True,
            high_pass_filter=True, auto_gain_control=True,
        )
        near = (np.sin(np.arange(FRAME_SAMPLES) * 0.2) * 8000).astype(np.int16)
        far = np.zeros(FRAME_SAMPLES, dtype=np.int16)
        out = apm.process(near, far)
        assert out.shape == near.shape and out.dtype == np.int16
        print(f"ok  AudioProcessor: {out.shape} int16, p(speech)={apm.speech_probability:.2f}")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL AudioProcessor: {exc}")
        ok = False

    try:
        import sounddevice as sd

        print(f"ok  input:  {sd.query_devices(kind='input')['name']}")
        print(f"ok  output: {sd.query_devices(kind='output')['name']}")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL devices: {exc}")
        ok = False

    if shutil.which("ffmpeg"):
        print("ok  ffmpeg")
    else:
        print("FAIL ffmpeg not on PATH")
        ok = False

    key = os.environ.get("GROQ_API_KEY") or (
        "GROQ_API_KEY=" in (Path.home() / ".heare" / ".env").read_text()
        if (Path.home() / ".heare" / ".env").exists()
        else False
    )
    print("ok  GROQ_API_KEY" if key else "FAIL no GROQ_API_KEY")
    ok = ok and bool(key)

    for mod in ("httpx", "edge_tts"):
        try:
            __import__(mod)
            print(f"ok  {mod}")
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {mod}: {exc}")
            ok = False

    print("\nready — run without --check to open the microphone" if ok else "\nnot ready")
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true", help="verify setup, do not open the mic")
    p.add_argument("--no-aec", action="store_true", help="control run: echo cancellation off")
    p.add_argument("--delay-ms", type=int, default=30, help="AEC stream delay hint")
    p.add_argument("--journal", default="experiments/spine/probe.jsonl")
    args = p.parse_args()

    if args.check:
        return check()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
