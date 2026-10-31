"""The voice, as a channel: sound in, a message out; a reply in, sound out.

One object is both the input and the output, because they cannot be
pulled apart: they share a device, and the microphone has to be told
what the speaker is playing or the assistant hears itself and can never
be talked over.

Everything about sound stays on this side. The core is told three
things — the person began, here is what they said, it was nothing — and
asks for three — say this, hold on, stop. Every piece behind that is the
old app's, carried over as it was: the device, the echo canceller, the
phrase detector, the turn clock, the wake gate, the sentence splitter,
the synthesiser, the mouth. Their constants were paid for one incident
at a time and are not reconsidered here.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, AsyncIterator

from heare import keys
from heare.channels.voice.hallucinations import is_junk
from heare.channels.voice.heard import Heard, levels
from heare.channels.voice.hearing import SILENT_AFTER_S, EarWatch, read
from heare.channels.voice.language import language_of
from heare.channels.voice.sentences import sentences, speakable
from heare.channels.voice.stt import transcribe
from heare.channels.voice.tts import synthesise
from heare.channels.voice.turn import TurnAssembler
from heare.channels.voice.utterance import Mouth
from heare.channels.voice.vad import EnergyVAD, loud_ms
from heare.channels.voice.voicing import voice_for_language
from heare.channels.voice.wake import WakeGate
from heare.channels.voice.wake_phrases import own_name, wake_phrases
from heare.core import Message, Port

logger = logging.getLogger("heare.voice")

# «Дякую» right after an answer is a person being polite; the same word
# into silence is the recogniser filling a gap.
COURTESY_WINDOW_S = 25.0
# Shorter than a spoken word: do not pay a recogniser to invent one.
MIN_SPEECH_MS = 240
# A voice over the speaker for this long is an interruption whatever it says.
INTERRUPT_HOLD_MS = 600.0
POLL_S = 0.1
# How often the ear is asked whether the device still calls. The threshold
# itself lives in hearing.py; this is only the cost of looking.
WATCHDOG_POLL_S = 1.0
# After one failed reopen, wait longer each time. A dead device must not
# become a reopen storm; the keeper would show a part that never rests.
WATCHDOG_RETRY_S = 30.0
WATCHDOG_RETRY_MAX_S = 120.0
# The waits between synthesiser attempts: empty audio is retried, four
# times is the documented policy, then the machine's own voice.
SYNTH_RETRY_S: tuple[float | None, ...] = (0.2, 0.5, 1.0, None)


@dataclass
class _Over:
    """A voice heard while the speaker was live, until it is understood."""

    heard_ms: float = 0.0
    speech_ms: float | None = None
    long: bool = False
    settled: bool = False


class Voice:
    name = "voice"
    abilities = frozenset({"pause"})

    def __init__(self, device: Any, settings: Any, *, aec: Any = None,
                 wake: WakeGate | None = None, voice: str = "",
                 heard: Heard | None = None,
                 watch_s: float = WATCHDOG_POLL_S,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.device = device
        self.heard = heard
        self.settings = settings
        self.aec = aec
        self.wake = wake
        self.mouth = Mouth(device)
        self.vad = EnergyVAD(stop_ms=int(_or(settings, "spine_vad_stop_ms", 600)))
        hold = float(_or(settings, "spine_turn_hold_seconds", 0.8))
        self.assembler = TurnAssembler(
            hold_s=hold,
            continuation_hold_s=float(
                _or(settings, "spine_turn_continuation_hold_seconds", hold * 2)
            ),
        )
        self._voice = voice
        self._over: _Over | None = None
        self._jobs: asyncio.Queue = asyncio.Queue()
        self._starts = 0
        # Which reply a phrase belongs to: the pieces of one share it, so a
        # recording can later be heard whole. Counted from the start time,
        # so a restart does not number replies over the old ones.
        self._turn = int(time.time() * 1000)
        self._reported = False
        self._spoke_at = 0.0
        self._port: Port | None = None
        self._watch_s = watch_s
        self._clock = clock
        self._ear = EarWatch()
        self._restart_at = 0.0
        self._retry_s = WATCHDOG_RETRY_S
        self._synthesis_failed = False

    @property
    def duplex(self) -> bool:
        """With a working canceller the microphone stays open while the
        assistant speaks, which is what lets the person cut in."""
        return self.aec is not None and bool(getattr(self.aec, "active", False))

    # -- controls ---------------------------------------------------------

    @property
    def controls(self) -> dict:
        device = self.device
        return {
            "mic": not getattr(device, "mute_input_user", False),
            "speaker": not getattr(device, "mute_output_user", False),
            "volume": float(getattr(device, "output_volume", 1.0)),
            # "judge": decides for itself about speech without the name;
            # "name": answers only when called.
            "listen": _or(self.settings, "listen", "judge"),
            "keep_heard": self._keeping,
        }

    @property
    def _keeping(self) -> bool:
        return self.heard is not None and _or(self.settings, "keep_heard", True) is not False

    def set_control(self, name: str, value) -> None:
        device = self.device
        if name == "mic":
            device.mute_input_user = not bool(value)
        elif name == "speaker":
            device.mute_output_user = not bool(value)
            if not value:
                self.stop()  # muted means silent now, not after this sentence
        elif name == "volume":
            device.output_volume = max(0.0, min(1.0, float(value)))
        elif name == "listen":
            if value not in ("judge", "name"):
                raise ValueError("listen is «judge» or «name»")
            self.settings.set("listen", value)
        elif name == "keep_heard":
            self.settings.set("keep_heard", bool(value))
        else:
            raise KeyError(name)

    # -- input ------------------------------------------------------------

    async def run(self, port: Port) -> None:
        self._port = port
        await asyncio.gather(
            self._listen(), self._recognise(), self._turns(), self._watch(),
        )

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(self._watch_s)
            await self._watch_once()

    async def _watch_once(self) -> None:
        """One reading of the ear: say an outage once, reopen at most once
        per cooldown, and never turn a dead device into a reopen storm."""
        now = self._clock()
        mute_user = bool(getattr(self.device, "mute_input_user", False))
        reading = read(self.device, muted=mute_user)
        said = self._ear.feed(reading, now=now)
        if said is not None:
            text, key = said
            logger.error("the microphone is deaf: %s", text)
            if self._port is not None:
                self._port.note(key, text)
        if not reading.deaf:
            self._retry_s = WATCHDOG_RETRY_S
            return
        if now < self._restart_at:
            return
        try:
            reopened = await self._restart_input()
        except Exception:  # noqa: BLE001 — the watchdog must stay alive
            logger.exception("the microphone could not be reopened")
            reopened = False
        now = self._clock()
        if reopened:
            # Judge the new stream from a fresh threshold; a stream that
            # opens but never calls must not be reopened again at once.
            self._restart_at = now + SILENT_AFTER_S
            self._retry_s = WATCHDOG_RETRY_S
        else:
            self._restart_at = now + self._retry_s
            self._retry_s = min(self._retry_s * 2.0, WATCHDOG_RETRY_MAX_S)

    async def _restart_input(self) -> bool:
        restart = getattr(self.device, "restart_input", None)
        if restart is None:
            return False
        return bool(await restart())

    async def _listen(self) -> None:
        while True:
            frame = await self.device.input_frames.get()
            # The microphone was muted and came back: what the detector
            # was holding belongs to the other side of that hole.
            if getattr(self.device, "take_input_gap", lambda: False)():
                self.vad.drop_in_flight()
            if self.aec is not None:
                frame = self.aec.process(frame)
            event = self.vad.feed(frame)
            over = self._over
            if over is not None and not over.settled and over.speech_ms is None:
                # Audio time and speech only: a real nod is followed by
                # the silence the detector waits through, and that must
                # not count as talking. Once END has frozen the reading,
                # later frames (a slow recogniser, a reply still playing)
                # must not turn a nod into an interruption.
                loud = getattr(self.vad, "loud_ms_in_speech", None)
                over.heard_ms = loud if loud is not None else over.heard_ms + len(frame) / 32.0
                over.long = over.long or over.heard_ms >= INTERRUPT_HOLD_MS
            if event is None:
                continue
            kind = getattr(event.kind, "value", event.kind)
            if kind == "start":
                self._starts += 1
                self.assembler.speech_started()
                self._reported = True
                if self.device.playing and self.duplex and self._over is None:
                    self._over = _Over()
                self._port.started(self.name)
            elif kind == "end":
                over = self._over
                if over is not None and not over.settled:
                    over.speech_ms = over.heard_ms
                    over.long = over.long or over.speech_ms >= INTERRUPT_HOLD_MS
                # One worker, in order: two phrases of one turn must reach
                # the assembler as they were said, whatever the network does.
                self._jobs.put_nowait((event.utterance, self._starts, over, self._turn))

    async def _recognise(self) -> None:
        while True:
            pcm, starts_then, over, turn = await self._jobs.get()
            text = ""
            try:
                text = await self._text_of(pcm, over=over is not None, turn=turn)
            except Exception:
                logger.exception("recognition failed")
            logger.info("heard: %r", text)
            if over is not None and not over.settled:
                over.settled = True
                if self._over is over:
                    self._over = None
                # The core decides what a voice over a reply meant. A nod
                # and a cough are not a turn. A port may answer directly
                # or await the brain; both are a verdict.
                verdict = self._port.said_over(
                    self.name, text, over.speech_ms or 0.0, over.long
                )
                if inspect.isawaitable(verdict):
                    verdict = await verdict
                if not verdict:
                    text = ""
            self.assembler.transcript(text, speech_resumed=self._starts > starts_then)

    async def _text_of(self, pcm: bytes, *, over: bool = False, turn: int = 0) -> str:
        loud = loud_ms(pcm)
        if loud < MIN_SPEECH_MS:
            return ""
        language = language_of(self.settings)
        about = {"loud_ms": round(loud), "over": over, "turn": turn, **levels(pcm),
                 "language": language}
        try:
            result = await transcribe(
                pcm, api_key=keys.get("GROQ_API_KEY") or "",
                language=language,
            )
        except Exception:
            self._keep(pcm, {**about, "text": "", "dropped": "failed"})
            raise
        text = (result.text or "").strip()
        recently = time.time() - self._spoke_at < COURTESY_WINDOW_S
        junk = is_junk(text, agent_spoke_recently=recently)
        self._keep(pcm, {**about, "text": text, "detected": result.language,
                         "dropped": "invented" if junk else None})
        if junk:
            logger.info("dropped an invented phrase: %r", text[:60])
            return ""
        return text

    def _keep(self, pcm: bytes, about: dict) -> None:
        # A file or two of a hundred kilobytes: not worth a thread.
        if self.heard is not None and self._keeping:
            self.heard.add(pcm, about)

    async def _turns(self) -> None:
        while True:
            await asyncio.sleep(POLL_S)
            turn = self.assembler.poll()
            if turn:
                self._turn += 1
                self._reported = False
                if self.wake is not None and not self.wake.accepts(turn):
                    logger.info("not addressed to me: %r", turn[:60])
                    if _or(self.settings, "listen", "judge") == "name":
                        self._port.overheard(self.name, turn)
                    else:
                        # No name: the assistant decides for itself
                        # whether this is worth a word.
                        self._port.heard(
                            Message(text=turn, channel=self.name, addressed=False)
                        )
                    continue
                self._port.heard(Message(text=turn, channel=self.name))
            elif self._reported and not self.assembler.open:
                # It began and came to nothing: silence, a cough, a nod.
                self._turn += 1
                self._reported = False
                self._port.nothing(self.name)

    # -- output -----------------------------------------------------------

    @property
    def speaking(self) -> bool:
        return self.mouth.speaking

    async def say(self, pieces: AsyncIterator[str]) -> str:
        utterance = self.mouth.open(self._synthesise, mute_mic=not self.duplex)
        self._synthesis_failed = False
        try:
            async for sentence in sentences(pieces):
                if utterance.stopped:
                    break
                # Markup and emoji are for eyes; a sentence with nothing
                # left to say never reaches the synthesiser.
                if not (spoken := speakable(sentence)):
                    continue
                self._spoke_at = time.time()
                if self.wake is not None:
                    self.wake.spoke()
                logger.info("say: %r", spoken)
                await utterance.say(spoken)
            await utterance.finish(timeout=60.0)
        finally:
            utterance.close()
        return "stopped" if utterance.stopped else "done"

    async def _synthesise(self, text: str) -> AsyncIterator[bytes]:
        """Four tries, then the machine's own voice. The synthesiser's
        service refuses a connection now and then, sometimes several times
        running, and a whole reply used to become the failure line — or
        silence — because of a handshake. An uglier voice is a far smaller
        failure than no voice."""
        # One voice for everything it says. The old rule picked a voice by
        # the letters of each sentence, so a sentence that was mostly
        # Latin — «Python, Go і Rust» — came out in another person's
        # voice, in the middle of a reply. The person's `language` decides
        # now, so English is spoken by the English voice; an unset setting
        # still resolves to Ukrainian.
        voice = (self._voice or _or(self.settings, "voice", "")
                 or voice_for_language(language_of(self.settings),
                                       fallback_lang="uk"))
        for wait in SYNTH_RETRY_S:
            sounded = False
            try:
                async for chunk in synthesise(text, voice=voice):
                    sounded = True
                    yield chunk
            except Exception:  # noqa: BLE001 — the fallback is the policy
                logger.exception("the synthesiser raised for %r", text[:40])
                sounded = False
            if sounded:
                return
            if wait is not None:
                logger.warning("no audio for %r — trying again", text[:40])
                await asyncio.sleep(wait)
        logger.warning("the synthesiser is refusing — using the system voice")
        self._report_synthesis_failed()
        pcm = await asyncio.to_thread(_system_voice, text)
        if pcm:
            yield pcm

    def _report_synthesis_failed(self) -> None:
        if self._synthesis_failed:
            return
        self._synthesis_failed = True
        if self._port is not None:
            self._port.note("tts", "failed")

    def pause(self) -> None:
        self.mouth.pause_all()

    def resume(self) -> None:
        self.mouth.resume_all()

    def stop(self) -> None:
        self.mouth.stop_all("barged")


def _system_voice(text: str) -> bytes:
    """The sentence in the machine's built-in voice, as 24 kHz PCM. Empty
    when there is no such voice or no ffmpeg — then it really is silence,
    and the core says so."""
    import shutil
    import subprocess
    import tempfile

    if not (shutil.which("say") and shutil.which("ffmpeg")):
        return b""
    try:
        listed = subprocess.run(["say", "-v", "?"], capture_output=True, text=True,
                                timeout=5).stdout
        cyrillic = any("\u0400" <= ch <= "\u04ff" for ch in text)
        wanted = "uk_UA" if cyrillic else "en_US"
        name = next((line.split("  ")[0].strip() for line in listed.splitlines()
                     if wanted in line), None)
        if name is None:
            return b""
        with tempfile.NamedTemporaryFile(suffix=".aiff") as out:
            subprocess.run(["say", "-v", name, "-o", out.name, text], check=True,
                           timeout=20)
            return subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", out.name,
                 "-ar", "24000", "-ac", "1", "-f", "s16le", "-"],
                capture_output=True, check=True, timeout=20,
            ).stdout
    except (subprocess.SubprocessError, OSError):
        return b""


def _or(settings: Any, name: str, default: Any) -> Any:
    value = getattr(settings, name, "")
    return default if value in ("", None) else value


def build(device: Any, settings: Any, *, wake: bool = True, aec: bool = True,
          watch_s: float = WATCHDOG_POLL_S) -> Voice:
    """The voice on a given device — a real one, or the listener's tape."""
    canceller = None
    if aec:
        from heare.channels.voice.aec import SpineAEC

        canceller = SpineAEC()
        # The speaker owns the reference: pushed from inside play(), it
        # is exactly what the room heard.
        device.far_sink = canceller
    gate = None
    if wake:
        gate = WakeGate(
            wake_phrases(settings, name=own_name(settings)),
            window_s=float(_or(settings, "wake_window_seconds", 45.0)),
            max_window_s=float(_or(settings, "wake_window_max_seconds", 180.0)),
            required=bool(_or(settings, "wake_required", True)),
        )
    return Voice(device, settings, aec=canceller, wake=gate, watch_s=watch_s)


async def open_voice(settings: Any, *,
                     watch_s: float = WATCHDOG_POLL_S) -> Voice:
    """The voice on this machine's microphone and speaker.

    A device that cannot open is not a reason to exit: the part stays
    alive, deaf, and the watchdog reopens it with backoff. The keeper
    would otherwise restart the same failure forever.
    """
    from heare.channels.voice.device import AudioIO

    from pathlib import Path

    device = AudioIO()
    try:
        await device.start()
    except Exception:  # noqa: BLE001 — deaf but alive is the contract
        logger.exception("the microphone did not open; staying alive deaf")
    voice = build(device, settings, watch_s=watch_s)
    voice.heard = Heard(Path(settings.heard_dir))
    return voice
