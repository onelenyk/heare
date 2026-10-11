"""The listener's own voice and its own ear.

It asks out loud and it hears the answer, so nothing between the
microphone and the speaker is taken on trust. The question is
synthesised (and kept on disk: the same words are asked every run, and
paying to synthesise them again proves nothing). The answer is the PCM
the tape recorded, handed to a recogniser that has never seen the text
it came from.

What it can say about an answer: what was said, in which language, when
the first sound came, whether it was cut, whether the audio is clean.
What it cannot: whether it is pleasant to listen to.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from heare import keys
from heare.channels.voice.stt import transcribe
from heare.channels.voice.tts import synthesise
from heare.listener.tape import IN_RATE, OUT_RATE, Sounded, Tape

CACHE = Path.home() / ".heare" / "listener"
# Not the assistant's own voice: an echo test that asks in the voice it
# answers in cannot tell the two apart.
ASKING_VOICE = "uk-UA-OstapNeural"
# Below this a sample is room tone, not speech (int16).
_QUIET = 300


def _trim(pcm: bytes) -> bytes:
    """Cut the silence the synthesiser pads a sentence with, so the end
    of the audio is the end of the last word."""
    a = np.frombuffer(pcm, dtype=np.int16)
    loud = np.flatnonzero(np.abs(a) > _QUIET)
    if not len(loud):
        return b""
    return a[max(0, loud[0] - 320) : loud[-1] + 320].tobytes()


async def spoken(text: str, voice: str = ASKING_VOICE) -> bytes:
    """``text`` as 16 kHz speech, the way a microphone would deliver it."""
    CACHE.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha1(f"{voice}|{text}".encode()).hexdigest()[:16]
    path = CACHE / f"{name}.pcm"
    if path.exists():
        return path.read_bytes()
    chunks = [c async for c in synthesise(text, voice=voice, sample_rate=IN_RATE)]
    pcm = _trim(b"".join(chunks))
    if not pcm:
        raise RuntimeError(f"the asking voice produced no audio for {text!r}")
    path.write_bytes(pcm)
    return pcm


def _to_16k(pcm: bytes) -> bytes:
    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if not len(a):
        return b""
    n = int(len(a) * IN_RATE / OUT_RATE)
    x = np.linspace(0, len(a) - 1, n)
    return np.interp(x, np.arange(len(a)), a).astype(np.int16).tobytes()


async def heard(pcm: bytes, language: str | None = "uk") -> str:
    """What a recogniser makes of audio the speaker was given."""
    key = keys.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("no GROQ_API_KEY: the listener has no ear")
    if not pcm:
        return ""
    return (await transcribe(_to_16k(pcm), api_key=key, language=language)).text


@dataclass
class Answer:
    """One question and everything measurable about what came back."""

    question: str
    text: str = ""
    # Seconds from the last word of the question to the first sound.
    first_sound: float | None = None
    # Seconds of audio that actually left the speaker.
    seconds: float = 0.0
    # Share of samples at full scale. Above a fraction of a percent the
    # voice crackles.
    clipped: float = 0.0
    rms: float = 0.0
    # The longest silence between two pieces of the reply.
    longest_gap: float = 0.0
    problems: list[str] = field(default_factory=list)

    def has(self, *words: str) -> bool:
        low = self.text.lower()
        return any(w.lower() in low for w in words)

    def line(self) -> str:
        first = "—" if self.first_sound is None else f"{self.first_sound:.2f} s"
        return (
            f"{self.question!r}\n    -> {self.text!r}\n"
            f"    перший звук {first} · звучало {self.seconds:.1f} s · "
            f"найдовша пауза {self.longest_gap:.2f} s · обрізано {self.clipped:.2%}"
            + (f"\n    ! {'; '.join(self.problems)}" if self.problems else "")
        )


def measure(question: str, pieces: list[Sounded], asked_until: float) -> Answer:
    answer = Answer(question)
    if not pieces:
        answer.problems.append("тиша: нічого не прозвучало")
        return answer
    pcm = b"".join(p.pcm for p in pieces)
    a = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).astype(np.float32)
    answer.seconds = len(a) / OUT_RATE
    answer.rms = float(np.sqrt(np.mean(a * a))) if len(a) else 0.0
    answer.clipped = float(np.mean(np.abs(a) >= 32700)) if len(a) else 0.0
    # The first *sound*, not the first buffer: a synthesiser's leading
    # silence is latency the person hears as latency.
    loud = np.flatnonzero(np.abs(a) > _QUIET)
    if not len(loud):
        answer.problems.append("тиша: звук був, але порожній")
        return answer
    answer.first_sound = pieces[0].at + loud[0] / OUT_RATE - asked_until
    for before, after in zip(pieces, pieces[1:]):
        answer.longest_gap = max(answer.longest_gap, after.at - before.ends)
    if answer.clipped > 0.005:
        answer.problems.append("звук обрізається на піках")
    if answer.rms < 200:
        answer.problems.append("надто тихо")
    return answer


async def ask(tape: Tape, question: str, *, settle: float = 1.5,
              timeout: float = 45.0, language: str | None = "uk") -> Answer:
    """Say ``question`` into the microphone and listen to the whole reply."""
    pcm = await spoken(question)
    mark = time.monotonic()
    tape.say(pcm)
    await tape.said()
    asked_until = tape.spoke_until
    try:
        await tape.until_sound(mark, timeout=timeout)
        await tape.until_quiet(settle=settle, timeout=timeout)
    except TimeoutError as exc:
        answer = measure(question, tape.since(mark), asked_until)
        answer.problems.append(str(exc))
        return answer
    pieces = tape.since(mark)
    answer = measure(question, pieces, asked_until)
    answer.text = await heard(b"".join(p.pcm for p in pieces), language)
    if not answer.text.strip() and not answer.problems:
        answer.problems.append("звук є, але слів у ньому не розпізнано")
    return answer


@dataclass
class Cut:
    """What happened when the person talked over a reply."""

    # Seconds from the first sound of the interruption to silence.
    quiet_after: float | None = None
    # What was said once the interruption was over, if anything.
    then_said: str = ""
    then_seconds: float = 0.0

    def line(self) -> str:
        quiet = "не замовк" if self.quiet_after is None else f"{self.quiet_after:.2f} s"
        return (f"перебивання: тиша через {quiet} · потім прозвучало "
                f"{self.then_seconds:.1f} s: {self.then_said!r}")


async def cut_in(tape: Tape, question: str, interruption: str, *,
                 after: float = 2.0, watch: float = 6.0) -> Cut:
    """Ask something long, talk over the answer, and listen to what happens.

    Silence is the first moment after the interruption began at which
    the speaker has nothing queued. Whatever sounds later is a second
    thing — an acknowledgement, or the reply resuming — and is heard
    separately, because "it stopped and then carried on" and "it stopped"
    are different results.
    """
    mark = time.monotonic()
    tape.say(await spoken(question))
    await tape.said()
    await tape.until_sound(mark)
    await asyncio.sleep(after)
    over = await spoken(interruption)
    began = time.monotonic()
    tape.say(over)
    cut = Cut()
    deadline = began + watch
    while time.monotonic() < deadline:
        if cut.quiet_after is None and not tape.playing:
            cut.quiet_after = time.monotonic() - began
        await asyncio.sleep(0.01)
    if cut.quiet_after is not None:
        later = [p for p in tape.sounded if p.at >= began + cut.quiet_after]
        pcm = b"".join(p.pcm for p in later)
        cut.then_seconds = len(pcm) / (OUT_RATE * 2)
        cut.then_said = await heard(pcm) if pcm else ""
    return cut
