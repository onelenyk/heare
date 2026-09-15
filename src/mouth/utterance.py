"""An utterance: one reply's worth of speech, and the handle to interrupt it.

Until this existed the loop kept a single boolean, ``_interrupted``, set
from three places (the ear on barge-in, the dashboard's Cancel, tests by
hand) and read in six. It described "the utterance being spoken", which
is why every freshly requested phrase had to clear it first — and when
one forgot, the flag became a latch that muted the next service phrase.
An interrupt belongs to the thing it interrupts. So the thing is an
object now, and the flag is a field on it.

What it can do that a boolean could not:

* **pause and resume.** The ear hears a voice while the speaker is live
  and needs the sound to stop *now*, before it knows whether it was
  interrupted or nodded at (docs/archive/2026-09-16/design.html §6). Pausing keeps what was
  going to be said; resuming replays from the start of the sentence that
  was cut, so «ага» costs a half-second and «то що я казав», not the
  rest of the reply.
* **say how it ended.** ``done``, ``barged``, ``cancelled``, ``wedged`` —
  the loop, the dashboard and the telemetry all wanted this and each
  read the boolean differently.

Nothing here knows about the model, the wake gate or the ledger. Those
are the loop's business and stay in ``loop._speak``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Callable

logger = logging.getLogger("heare.mouth")

Synthesise = Callable[[str], AsyncIterator[bytes]]


class Utterance:
    """Sentences go in through ``say``; the speaker gets them as they are
    synthesised; ``finish`` waits for the last of them to leave the room."""

    def __init__(self, mouth: Mouth, synthesise: Synthesise, *, mute_mic: bool) -> None:
        self._mouth = mouth
        self._speaker = mouth.speaker
        self._synthesise = synthesise
        self.stopped = False
        self.paused = False
        self.outcome: str | None = None
        # Audio per sentence, kept so a paused utterance can be resumed
        # from the start of the sentence that was cut. A thirty-second
        # reply is about a megabyte and a half; it is freed on close().
        self._sentences: list[bytearray] = []
        # Bytes this utterance has handed the speaker, so a stop() that
        # reports how much it dropped tells us how far playback got.
        self._sent = 0
        self._resume_from = 0
        self._closed = False
        self._mute_mic = mute_mic
        if mute_mic:
            # Half duplex: the microphone is deaf for the whole utterance —
            # from before the first sound, through tool calls, until the
            # speaker drains — not from the first sentence. The thinking
            # gap is the part a stray echo would otherwise land in.
            self._speaker.mute_input = True

    # -- speaking -------------------------------------------------------

    async def say(self, sentence: str) -> None:
        """Synthesise one sentence and feed the speaker as chunks arrive.

        Returns when synthesis is done, not when playback is: the caller
        goes on to the next sentence while this one is still sounding,
        which is what keeps the first sound ~0.2 s behind the first token.
        Stopped mid-way, it returns without a fuss — the words still count
        as said for history, which is the caller's decision, not this one's.
        """
        if self.stopped:
            return
        if self._muted():
            # A muted speaker drops the bytes at play(); synthesising
            # them first cost a network round trip per sentence and made
            # a muted reply slower than a spoken one. Nothing to keep for
            # resume either — there is nothing to resume into.
            return
        pcm = bytearray()
        self._sentences.append(pcm)
        async for chunk in self._synthesise(sentence):
            if self.stopped:
                break
            pcm += chunk
            if not self.paused:
                self._speaker.play(chunk)
                self._sent += len(chunk)

    def _muted(self) -> bool:
        """The speaker's own mute — a quiet role's, or the dashboard's —
        read where the bytes would have been dropped anyway."""
        return bool(
            getattr(self._speaker, "mute_output", False)
            or getattr(self._speaker, "mute_output_user", False)
        )

    # -- interrupting ---------------------------------------------------

    def pause(self) -> None:
        """Silence the speaker now; keep everything not yet heard.

        The far-end reference is cleared with the dropped audio (that is
        what ``stop_playback`` does), so the canceller is not left
        subtracting an echo of sound the room never heard.
        """
        if self.stopped or self.paused:
            return
        dropped = self._speaker.stop_playback()
        played = max(0, self._sent - dropped)
        # Which sentence was sounding when the speaker went quiet. Resume
        # replays it from its start: a sentence cut in half and continued
        # from the cut is the one thing that sounds worse than a repeat.
        at = 0
        index = 0
        for index, sentence in enumerate(self._sentences):
            if played < at + len(sentence):
                break
            at += len(sentence)
        else:
            index = len(self._sentences)
        self._resume_from = index
        self.paused = True

    def resume(self) -> None:
        """Take the pause back: replay from the start of the cut sentence,
        then everything queued behind it, then whatever is still coming."""
        if self.stopped or not self.paused:
            return
        self.paused = False
        index = min(self._resume_from, len(self._sentences))
        self._sent = sum(len(s) for s in self._sentences[:index])
        for sentence in self._sentences[index:]:
            if sentence:
                self._speaker.play(bytes(sentence))
                self._sent += len(sentence)

    def stop(self, outcome: str = "barged") -> int:
        """End it. Returns the bytes that never reached the room."""
        if self.stopped:
            return 0
        self.stopped = True
        self.paused = False
        self.outcome = outcome
        return self._speaker.stop_playback()

    # -- ending ---------------------------------------------------------

    async def finish(self, timeout: float = 60.0) -> str:
        """Wait for the speaker to drain (or for a pause to be resolved),
        then release the microphone. Says how the utterance ended."""
        try:
            try:
                await asyncio.wait_for(self._drained(), timeout=timeout)
            except asyncio.TimeoutError:
                # A stalled output device must not wedge the loop forever.
                dropped = self._speaker.stop_playback()
                logger.warning("playback wedged — dropped %d queued bytes", dropped)
                if not self.stopped:
                    self.stopped, self.outcome = True, "wedged"
        finally:
            self.close()
        if self.outcome is None:
            self.outcome = "done"
        return self.outcome

    async def _drained(self) -> None:
        while not self.stopped and (self.paused or self._speaker.playing):
            await asyncio.sleep(0.05)

    def close(self) -> None:
        """Give the microphone back and let the audio go. Idempotent, and
        the one thing every exit path must reach."""
        if self._closed:
            return
        self._closed = True
        if self._mute_mic:
            self._speaker.mute_input = False
        self._sentences.clear()
        self._mouth._forget(self)


class Mouth:
    """Opens utterances on one speaker and can reach all of them at once.

    Two can be live together — a role's closing line spoken from its own
    task while a reply is still draining — and an interruption from the
    room has to reach both, which is why they are opened here and not
    made ad hoc.
    """

    def __init__(self, speaker: Any) -> None:
        self.speaker = speaker
        self._live: list[Utterance] = []
        self.last: Utterance | None = None

    def open(self, synthesise: Synthesise, *, mute_mic: bool = False) -> Utterance:
        utterance = Utterance(self, synthesise, mute_mic=mute_mic)
        self._live.append(utterance)
        self.last = utterance
        return utterance

    def _forget(self, utterance: Utterance) -> None:
        if utterance in self._live:
            self._live.remove(utterance)

    @property
    def speaking(self) -> bool:
        return bool(self._live) and bool(getattr(self.speaker, "playing", False))

    @property
    def interrupted(self) -> bool:
        """Whether the most recent utterance was cut short. Describes that
        utterance only — a fresh one starts clean, so there is no latch."""
        return self.last is not None and self.last.stopped

    def pause_all(self) -> None:
        for utterance in list(self._live):
            utterance.pause()

    def resume_all(self) -> None:
        for utterance in list(self._live):
            utterance.resume()

    def stop_all(self, outcome: str = "barged") -> int:
        """Stop every live utterance; returns the bytes dropped in total.
        With nothing live it still clears the speaker, for the one caller
        that may have queued sound without an utterance: nobody, now —
        but a Cancel pressed into silence must cost nothing either."""
        if not self._live:
            return self.speaker.stop_playback()
        return sum(u.stop(outcome) for u in list(self._live))
