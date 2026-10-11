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
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

from heare import keys
from heare.channels.voice.hallucinations import is_junk
from heare.channels.voice.sentences import sentences, speakable
from heare.channels.voice.stt import transcribe
from heare.channels.voice.tts import synthesise
from heare.channels.voice.turn import TurnAssembler
from heare.channels.voice.utterance import Mouth
from heare.channels.voice.vad import EnergyVAD, loud_ms
from heare.channels.voice.voicing import pick_voice
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
                 wake: WakeGate | None = None, voice: str = "") -> None:
        self.device = device
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
        self._reported = False
        self._spoke_at = 0.0
        self._port: Port | None = None

    @property
    def duplex(self) -> bool:
        """With a working canceller the microphone stays open while the
        assistant speaks, which is what lets the person cut in."""
        return self.aec is not None and bool(getattr(self.aec, "active", False))

    # -- input ------------------------------------------------------------

    async def run(self, port: Port) -> None:
        self._port = port
        await asyncio.gather(self._listen(), self._recognise(), self._turns())

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
            if over is not None and not over.settled:
                # Audio time and speech only: a real nod is followed by
                # the silence the detector waits through, and that must
                # not count as talking.
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
                # One worker, in order: two phrases of one turn must reach
                # the assembler as they were said, whatever the network does.
                self._jobs.put_nowait((event.utterance, self._starts, over))

    async def _recognise(self) -> None:
        while True:
            pcm, starts_then, over = await self._jobs.get()
            text = ""
            try:
                text = await self._text_of(pcm)
            except Exception:
                logger.exception("recognition failed")
            logger.info("heard: %r", text)
            if over is not None and not over.settled:
                over.settled = True
                if self._over is over:
                    self._over = None
                # The core decides what a voice over a reply meant. A nod
                # and a cough are not a turn.
                if not self._port.said_over(
                    self.name, text, over.speech_ms or 0.0, over.long
                ):
                    text = ""
            self.assembler.transcript(text, speech_resumed=self._starts > starts_then)

    async def _text_of(self, pcm: bytes) -> str:
        if loud_ms(pcm) < MIN_SPEECH_MS:
            return ""
        result = await transcribe(
            pcm, api_key=keys.get("GROQ_API_KEY") or "",
            language=_or(self.settings, "groq_language", "uk"),
        )
        text = (result.text or "").strip()
        recently = time.time() - self._spoke_at < COURTESY_WINDOW_S
        if is_junk(text, agent_spoke_recently=recently):
            logger.info("dropped an invented phrase: %r", text[:60])
            return ""
        return text

    async def _turns(self) -> None:
        while True:
            await asyncio.sleep(POLL_S)
            turn = self.assembler.poll()
            if turn:
                self._reported = False
                if self.wake is not None and not self.wake.accepts(turn):
                    logger.info("not addressed to me: %r", turn[:60])
                    self._port.nothing(self.name)
                    continue
                self._port.heard(Message(text=turn, channel=self.name))
            elif self._reported and not self.assembler.open:
                # It began and came to nothing: silence, a cough, a nod.
                self._reported = False
                self._port.nothing(self.name)

    # -- output -----------------------------------------------------------

    @property
    def speaking(self) -> bool:
        return self.mouth.speaking

    async def say(self, pieces: AsyncIterator[str]) -> str:
        utterance = self.mouth.open(self._synthesise, mute_mic=not self.duplex)
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
        """One retry. The synthesiser's service refuses a connection now
        and then, and without this a whole reply became the failure line
        because of one handshake."""
        voice = self._voice or pick_voice(text, fallback_lang="uk")
        for attempt in (1, 2):
            sounded = False
            async for chunk in synthesise(text, voice=voice):
                sounded = True
                yield chunk
            if sounded:
                return
            if attempt == 1:
                logger.warning("no audio for %r — trying once more", text[:40])
                await asyncio.sleep(0.3)

    def pause(self) -> None:
        self.mouth.pause_all()

    def resume(self) -> None:
        self.mouth.resume_all()

    def stop(self) -> None:
        self.mouth.stop_all("barged")


def _or(settings: Any, name: str, default: Any) -> Any:
    value = getattr(settings, name, "")
    return default if value in ("", None) else value


def build(device: Any, settings: Any, *, wake: bool = True, aec: bool = True) -> Voice:
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
    return Voice(device, settings, aec=canceller, wake=gate)


async def open_voice(settings: Any) -> Voice:
    """The voice on this machine's microphone and speaker."""
    from heare.channels.voice.device import AudioIO

    device = AudioIO()
    await device.start()
    return build(device, settings)
