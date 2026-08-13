"""The conductor — plain asyncio, no framework.

Every collaborator is injected as a callable or a small object, and this
file imports no sibling module: the loop owns order and lifecycle, the
modules own their I/O. That is what lets the whole conversation be tested
with fakes before a single audio device or network socket exists.

Half-duplex v0: the microphone is muted while the assistant speaks, so
echo cancellation is not needed for the skeleton to hold a conversation.
Barge-in by voice is therefore impossible in v0 — a deliberate debt,
listed in the plan, paid when the AEC moves in.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol

logger = logging.getLogger("spine.loop")

DEFAULT_SYSTEM_PROMPT = (
    "Ти голосовий асистент на ім'я heare. Тебе слухають вухами, а не "
    "читають: відповідай коротко, українською, простою прозою — без "
    "розмітки, списків і коду. Одна-три фрази, як у живій розмові."
)

# The generator keeps only a short tail of the dialogue: the skeleton has
# no summarisation yet, and an unbounded history would slowly push the
# system prompt out of the model's attention.
HISTORY_TURNS = 12


class AudioLike(Protocol):
    input_frames: asyncio.Queue
    mute_input: bool

    def play(self, pcm: bytes) -> None: ...
    def stop_playback(self) -> int: ...
    @property
    def playing(self) -> bool: ...


@dataclass
class SpineLoop:
    """Wires ear → turn → mind → mouth. All parts injected."""

    audio: AudioLike | None
    vad: Any  # .feed(frame) -> event with .kind.value in {"start","end"} | None
    assembler: Any  # .speech_started() / .transcript(text) / .poll() -> str | None
    transcribe: Callable[[bytes], Awaitable[Any]]  # -> object with .text
    stream_chat: Callable[[list[dict]], AsyncIterator[str]]
    split_sentences: Callable[[AsyncIterator[str]], AsyncIterator[str]]
    synthesise: Callable[[str], AsyncIterator[bytes]]
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    poll_interval: float = 0.1
    history: list[dict] = field(default_factory=list)
    _stt_jobs: asyncio.Queue = field(default_factory=asyncio.Queue)
    # Speech-start counter: each STT job carries the count at its END so a
    # slow transcription can tell whether the user has started talking
    # again since — see TurnAssembler.transcript(speech_resumed=...).
    _starts_seen: int = 0

    async def run(self) -> None:
        """Listen and answer until cancelled."""
        tasks = [
            asyncio.create_task(self._listen(), name="spine-listen"),
            asyncio.create_task(self._stt_worker(), name="spine-stt"),
            asyncio.create_task(self._converse(), name="spine-converse"),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            # cancel() only schedules; without this await the caller's own
            # teardown (closing audio streams, killing ffmpeg) races the
            # children's cleanup.
            await asyncio.gather(*tasks, return_exceptions=True)

    # -- ear ----------------------------------------------------------

    async def _listen(self) -> None:
        if self.audio is None:
            return
        while True:
            frame = await self.audio.input_frames.get()
            event = self.vad.feed(frame)
            if event is None:
                continue
            kind = getattr(event.kind, "value", event.kind)
            if kind == "start":
                self._starts_seen += 1
                self.assembler.speech_started()
            elif kind == "end":
                # Off the hot path — but through a single worker, not
                # fire-and-forget: two utterances in one turn must reach
                # the assembler in speech order even when the network
                # returns them in the other one.
                self._stt_jobs.put_nowait((event.utterance, self._starts_seen))

    async def _stt_worker(self) -> None:
        while True:
            pcm, starts_at_end = await self._stt_jobs.get()
            await self._transcribe(pcm, starts_at_end)

    async def _transcribe(self, pcm: bytes, starts_at_end: int) -> None:
        try:
            result = await self.transcribe(pcm)
        except Exception:
            logger.exception("stt failed")
            # An utterance that produced no text must still release the
            # turn the assembler is holding open for it.
            self.assembler.transcript(
                "", speech_resumed=self._starts_seen > starts_at_end
            )
            return
        text = (getattr(result, "text", None) or "").strip()
        logger.info("heard: %r", text)
        self.assembler.transcript(
            text, speech_resumed=self._starts_seen > starts_at_end
        )

    # -- turn → mind → mouth ------------------------------------------

    async def _converse(self) -> None:
        while True:
            await asyncio.sleep(self.poll_interval)
            turn = self.assembler.poll()
            if not turn:
                continue
            try:
                await self.respond(turn)
            except Exception:
                logger.exception("turn failed: %r", turn[:80])

    async def respond(self, user_text: str, *, speak: bool = True) -> str:
        """One full exchange. Returns the reply text (also spoken)."""
        self.history.append({"role": "user", "content": user_text})
        messages = [
            {"role": "system", "content": self.system_prompt},
            *self.history[-HISTORY_TURNS:],
        ]

        parts: list[str] = []

        async def _deltas() -> AsyncIterator[str]:
            async for delta in self.stream_chat(messages):
                parts.append(delta)
                yield delta

        speaking = speak and self.audio is not None
        if speaking:
            self.audio.mute_input = True
        try:
            async for sentence in self.split_sentences(_deltas()):
                logger.info("say: %r", sentence)
                if speaking:
                    async for chunk in self.synthesise(sentence):
                        self.audio.play(chunk)
            if speaking:
                # play() only queues; stay muted until the buffer drains
                # or the tail of our own voice becomes the next "user".
                # Bounded: a stalled output device must not leave the mic
                # muted forever.
                try:
                    await asyncio.wait_for(self._drain_playback(), timeout=60.0)
                except asyncio.TimeoutError:
                    dropped = self.audio.stop_playback()
                    logger.warning(
                        "playback wedged — dropped %d queued bytes", dropped
                    )
        finally:
            if speaking:
                self.audio.mute_input = False

        reply = "".join(parts).strip()
        if reply:
            self.history.append({"role": "assistant", "content": reply})
        return reply

    async def _drain_playback(self) -> None:
        while self.audio is not None and self.audio.playing:
            await asyncio.sleep(0.05)
