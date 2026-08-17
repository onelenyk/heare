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

# Said out loud when a turn dies on infrastructure (the model, the mouth,
# anything below them). The whole history of this project is failures that
# look like silence, and silence is indistinguishable from "it did not hear
# me" — so a broken turn must still end with a sound.
FAILURE_LINE = "Щось пішло не так — не змогла відповісти."

# The generator keeps only a short tail of the dialogue: the skeleton has
# no summarisation yet, and an unbounded history would slowly push the
# system prompt out of the model's attention.
HISTORY_TURNS = 12

# The role platform used to be nine flat fields here. It is one object
# now, but the daemon and the CLI still set and read the old names, so
# they stay as views onto the flow: old name -> field on the flow.
_ROLE_ATTRS = {
    "roles": "roles", "role_manager": "role_manager",
    "trigger_match": "trigger_match", "end_match": "end_match",
    "save_artifact": "save_artifact", "hint_sink": "hint_sink",
    "role_finishing": "finishing", "_role_log": "log",
    "_role_ended_ts": "ended_ts",
}


class _MouthFailed(Exception):
    """Marker: the speaking path failed (TTS, ffmpeg, the sound device),
    not the model. The distinction matters for history — the words were
    produced, only the voice for them was not."""


class AudioLike(Protocol):
    input_frames: asyncio.Queue
    mute_input: bool

    def play(self, pcm: bytes) -> None: ...
    def stop_playback(self) -> int: ...
    @property
    def playing(self) -> bool: ...


@dataclass
class SpineLoop:
    """Wires ear → turn → mind → mouth. All parts injected.

    Optional collaborators (None = feature off, v0 behaviour):
    aec — echo canceller; when active the mic stays OPEN while speaking
    (full duplex) and a VAD start during playback interrupts the mouth.
    wake — gate deciding whether a closed turn reaches the LLM at all.
    toolbox + stream_events — the three voice verbs; without them the
    loop is chat-only. make_system_prompt — per-turn prompt (persona,
    memory, recent exchanges) instead of the static default. persist /
    usage — SQLite logging of turns and paid calls; both must never
    break the conversation, so their errors are swallowed and logged.
    role_flow — the role platform: the conductor asks it whether a turn
    belongs to a session and hands it every voice exchange; the policy
    itself (triggers, quiet channels, artifacts) lives in that object.
    """

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
    aec: Any = None            # .process(frame)->frame, .push_far(pcm), .active
    wake: Any = None           # .accepts(turn_text) -> bool
    toolbox: Any = None        # .schemas, .execute(name, dict) -> spoken str
    stream_events: Any = None  # (messages, tools) -> AsyncIterator[dict]
    make_system_prompt: Any = None  # async () -> str
    persist: Any = None        # .log_user_turn/.log_agent_reply (sync)
    engine: Any = None         # .run(), .observe_reply(text) — holds what
                               # outlives a turn and decides when to speak
                               # first. Injected like the rest: the loop
                               # conducts, it does not want anything.
    usage: Any = None          # .stt(seconds) / .tts(chars) (sync)
    # The role platform, as one injected policy object (None = no roles).
    # It decides whether a turn is a session trigger, a logged line or
    # ordinary conversation; the conductor only asks. Its fields are
    # reachable under their old names through this loop — see _ROLE_ATTRS
    # — so wiring written against the old flat fields keeps working.
    role_flow: Any = None      # .in_session / await .handle(turn) -> bool
    _stt_jobs: asyncio.Queue = field(default_factory=asyncio.Queue)
    # Turns injected from outside the microphone (Hands results, the
    # dashboard). They are already addressed to the assistant, so they
    # bypass the wake gate.
    _injected: asyncio.Queue = field(default_factory=asyncio.Queue)
    # Speech-start counter: each STT job carries the count at its END so a
    # slow transcription can tell whether the user has started talking
    # again since — see TurnAssembler.transcript(speech_resumed=...).
    _starts_seen: int = 0
    # When the assistant last put audio on the speaker. The junk filter
    # asks: is there anyone to say «дякую» to right now?
    last_spoke_ts: float = 0.0
    # The dashboard's interrupt switch. Off means the assistant finishes
    # its sentence even when talked over — some rooms want that.
    barge_in_enabled: bool = True
    # Set when the user starts talking over the assistant (full duplex
    # only) or when the dashboard's Cancel is pressed; respond() checks it
    # between chunks and stops feeding the speaker, while still collecting
    # the full reply text for history. It describes the utterance being
    # spoken, so every freshly requested one — respond(), _say_now() —
    # clears it first; otherwise it is a latch that mutes the assistant.
    _interrupted: bool = False

    @property
    def _duplex(self) -> bool:
        """Full duplex only when a live canceller protects the mic."""
        return self.aec is not None and getattr(self.aec, "active", False)

    # -- the role platform: adopted, then delegated to ------------------

    def adopt_role_flow(self, flow: Any) -> Any:
        """Lend a role flow the conductor's mouth, then own it: the loop
        imports no sibling, so the policy object is built outside and
        handed in here with the callbacks only the loop can give. Role
        collaborators set on the loop before this lands move over."""
        flow.say = self._say_now
        flow.persist_turn = self._persist_user_turn
        flow.stream_chat = lambda messages: self.stream_chat(messages)
        flow.get_muted = self._output_muted
        flow.set_muted = self._set_output_mute
        for name, mapped in _ROLE_ATTRS.items():
            if name in self.__dict__:
                setattr(flow, mapped, self.__dict__.pop(name))
        self.role_flow = flow
        return flow

    def __getattr__(self, name: str) -> Any:
        # Reached only for names no field holds — the old role fields.
        mapped = _ROLE_ATTRS.get(name)
        flow = self.__dict__.get("role_flow")
        if mapped is None or flow is None:
            raise AttributeError(name)
        return getattr(flow, mapped)

    def __setattr__(self, name: str, value: Any) -> None:
        flow = self.__dict__.get("role_flow")
        mapped = _ROLE_ATTRS.get(name) if flow is not None else None
        if mapped is None:
            object.__setattr__(self, name, value)  # a field of the loop
        else:
            setattr(flow, mapped, value)

    async def run(self) -> None:
        """Listen and answer until cancelled."""
        tasks = [
            asyncio.create_task(self._listen(), name="spine-listen"),
            asyncio.create_task(self._stt_worker(), name="spine-stt"),
            asyncio.create_task(self._converse(), name="spine-converse"),
        ]
        if self.engine is not None:
            tasks.append(asyncio.create_task(self.engine.run(), name="spine-engine"))
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            # cancel() only schedules; without this await the caller's own
            # teardown (closing audio streams, killing ffmpeg) races the
            # children's cleanup.
            await asyncio.gather(*tasks, return_exceptions=True)
            if self.toolbox is not None:
                try:
                    self.toolbox.cancel_all()
                except Exception:
                    logger.debug("toolbox cancel_all failed (non-fatal)")

    # -- ear ----------------------------------------------------------

    async def _listen(self) -> None:
        if self.audio is None:
            return
        while True:
            frame = await self.audio.input_frames.get()
            # The mic was muted and came back: whatever the VAD was
            # holding belongs to the other side of that hole.
            if getattr(self.audio, "take_input_gap", lambda: False)():
                dropped = self.vad.drop_in_flight()
                if dropped:
                    logger.info("mic gap — dropped %d bytes in flight", dropped)
            if self.aec is not None:
                frame = self.aec.process(frame)
            event = self.vad.feed(frame)
            if event is None:
                continue
            kind = getattr(event.kind, "value", event.kind)
            if kind == "start":
                self._starts_seen += 1
                self.assembler.speech_started()
                if self.audio.playing and self._duplex and self.barge_in_enabled:
                    # Barge-in: the user talks over the assistant. Drop
                    # what is queued and tell respond() to stop feeding.
                    self._interrupted = True
                    # stop_playback also clears the far-end reference:
                    # bytes that never leave the speaker must not be
                    # subtracted as echo from the very utterance that
                    # interrupted them.
                    dropped = self.audio.stop_playback()
                    logger.info("barge-in: dropped %d queued bytes", dropped)
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

    async def inject(self, text: str) -> None:
        """Queue a turn that did not come from the microphone."""
        self._injected.put_nowait(text)

    async def _converse(self) -> None:
        while True:
            await asyncio.sleep(self.poll_interval)
            gated = True
            if not self._injected.empty():
                turn = self._injected.get_nowait()
                gated = False
            else:
                turn = self.assembler.poll()
            if not turn:
                continue
            in_role = self.role_flow is not None and self.role_flow.in_session
            if gated and not in_role and (
                self.wake is not None and not self.wake.accepts(turn)
            ):
                # In a session the whole room is the point — see
                # RoleFlow.in_session; outside one the wake gate rules.
                logger.info("asleep — turn not addressed to me: %r", turn[:60])
                continue
            try:
                flow = self.role_flow
                if flow is not None and await flow.handle(turn, injected=not gated):
                    continue  # a role session consumed the turn
                await self.respond(turn)
            except Exception:
                logger.exception("turn failed: %r", turn[:80])

    async def _persist_user_turn(self, text: str) -> int | None:
        """Log a heard turn; None when no persistence is wired. Errors
        are the caller's to swallow — never a reason to stop talking."""
        if self.persist is None:
            return None
        return await asyncio.to_thread(self.persist.log_user_turn, text)

    def _output_muted(self) -> bool:
        return bool(getattr(self.audio, "mute_output", False))

    def _set_output_mute(self, muted: bool) -> None:
        """The speaker's mute — a quiet role records without speaking."""
        if self.audio is not None:
            self.audio.mute_output = muted

    async def _say_now(self, text: str) -> None:
        """Speak a short service phrase outside the LLM flow. It is still
        part of the conversation, so it lands in history too."""
        if not text:
            return
        # The interrupt belongs to the utterance being spoken, not to the
        # loop. A service phrase is requested fresh, in the present tense,
        # so an interrupt left over from before it existed — the dashboard
        # Cancel pressed while nothing was playing, a barge-in whose own
        # speech the junk filter then dropped — must not silence it. It
        # used to: the ack landed in history as if said, and the room
        # heard nothing. Only respond() cleared this flag.
        self._interrupted = False
        logger.info("say (service): %r", text[:70])
        self.history.append({"role": "assistant", "content": text})
        if self.audio is None:
            return
        try:
            await self._speak(text)
            await asyncio.wait_for(self._drain_playback(), timeout=30.0)
        except asyncio.TimeoutError:
            self.audio.stop_playback()

    async def respond(self, user_text: str, *, speak: bool = True) -> str:
        """One full exchange. Returns the reply text (also spoken)."""
        self._interrupted = False
        if self.engine is not None:
            # Whatever they just said is also the verdict on whatever it
            # last brought up unbidden. Reading it here is the whole
            # safeguard on speaking freely: being brushed off has to cost
            # something, or "freely" becomes "constantly" within a day.
            try:
                await self.engine.observe_reply(user_text)
            except Exception:
                logger.exception("engine: reading the reply failed (non-fatal)")
        turn_id: int | None = None
        try:
            turn_id = await self._persist_user_turn(user_text)
        except Exception:
            logger.exception("persist failed (non-fatal)")

        # History first: the prompt builder greps memory by history[-1],
        # which must be the user's current question, not the assistant's
        # own previous reply.
        self.history.append({"role": "user", "content": user_text})

        prompt = self.system_prompt
        if self.make_system_prompt is not None:
            try:
                prompt = await self.make_system_prompt()
            except Exception:
                logger.exception("prompt build failed — using default")

        messages = [
            {"role": "system", "content": prompt},
            *self.history[-HISTORY_TURNS:],
        ]

        parts: list[str] = []
        tool_calls: list[dict] = []

        async def _deltas() -> AsyncIterator[str]:
            if self.stream_events is not None and self.toolbox is not None:
                events = self.stream_events(messages, self.toolbox.schemas)
                async for ev in events:
                    if self._interrupted:
                        break  # stop paying for a reply nobody will hear
                    if ev.get("type") == "delta":
                        parts.append(ev["text"])
                        yield ev["text"]
                    elif ev.get("type") == "tool_call":
                        tool_calls.append(ev)
                    elif ev.get("type") == "usage" and self.usage is not None:
                        try:
                            await asyncio.to_thread(
                                self.usage.llm,
                                ev.get("model") or "unknown",
                                ev.get("input_tokens", 0),
                                ev.get("output_tokens", 0),
                            )
                        except Exception:
                            logger.debug("usage.llm failed (non-fatal)")
            else:
                async for delta in self.stream_chat(messages):
                    parts.append(delta)
                    yield delta

        speaking = speak and self.audio is not None
        half_duplex = speaking and not self._duplex
        if half_duplex:
            self.audio.mute_input = True

        # None = the turn completed; "mind" = the model (or the prompt
        # path below it) died; "mouth" = the words existed but could not
        # be voiced. An infrastructure failure never leaves respond() as
        # an exception: the user gets a sentence, not silence.
        failure: str | None = None
        try:
            try:
                async for sentence in self.split_sentences(_deltas()):
                    logger.info("say: %r", sentence)
                    if speaking and not self._interrupted:
                        await self._speak_guarded(sentence)

                # The model answered with actions instead of (or besides)
                # words: run them and speak each short acknowledgement.
                # A barge-in cancels pending actions too — "стоп" must not
                # be followed by the very delegation it was stopping.
                for call in tool_calls:
                    if self._interrupted:
                        break
                    spoken = await self._run_tool(call)
                    if spoken:
                        parts.append((" " if parts else "") + spoken)
                        if speaking and not self._interrupted:
                            await self._speak_guarded(spoken)

                if speaking:
                    # play() only queues; wait for the buffer to drain (in
                    # half duplex the mic stays muted until then). Bounded: a
                    # stalled output device must not wedge the loop forever.
                    try:
                        await asyncio.wait_for(self._drain_playback(), timeout=60.0)
                    except asyncio.TimeoutError:
                        dropped = self.audio.stop_playback()
                        logger.warning(
                            "playback wedged — dropped %d queued bytes", dropped
                        )
            finally:
                if half_duplex:
                    self.audio.mute_input = False
        except asyncio.CancelledError:
            # Shutdown, not a fault: it must reach the task that cancelled
            # us, and it must not be answered with an apology out loud.
            raise
        except _MouthFailed:
            logger.exception("mouth failed: reply produced but not voiced")
            failure = "mouth"
        except Exception:
            logger.exception("mind failed: no reply for %r", user_text[:80])
            failure = "mind"

        # A mouth failure keeps whatever the model said — those words
        # existed, and the next prompt must see the answer the user was
        # (partly) given. A mind failure throws its fragment away: half a
        # sentence cut by a reset stream is not an answer, and carrying it
        # forward would make every later prompt quote a truncated one.
        reply = "" if failure == "mind" else "".join(parts).strip()

        # What the assistant's turn IS, for history, the DB and a role
        # transcript alike. A failed turn is recorded rather than erased:
        # the user heard something (the failure line), the DB already has
        # the user's row, and leaving that row unanswered is the same lie
        # in the database that silence is in the room.
        recorded = reply or (FAILURE_LINE if failure is not None else "")
        try:
            if reply:
                self.history.append({"role": "assistant", "content": reply})
            if failure is not None:
                # Goes through the service-phrase path, which also lands
                # the line in history — so no user turn is ever left
                # unanswered there.
                await self._say_failure_line(speak)
            if recorded and self.persist is not None and turn_id is not None:
                try:
                    await asyncio.to_thread(
                        self.persist.log_agent_reply, recorded, turn_id
                    )
                except Exception:
                    logger.exception("persist failed (non-fatal)")
        finally:
            # In a role session the turns row exists the moment the user
            # spoke; if the exchange never reaches the flow, the artifact
            # silently omits a turn the database has. It runs on every
            # exit, failure included.
            if self.role_flow is not None:
                try:
                    self.role_flow.note_exchange(user_text, recorded, turn_id)
                except Exception:
                    logger.exception("note_exchange failed (non-fatal)")
        return reply

    async def _speak_guarded(self, sentence: str) -> None:
        """_speak, with its failures labelled as the mouth's."""
        try:
            await self._speak(sentence)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise _MouthFailed(sentence[:60]) from exc

    async def _say_failure_line(self, speak: bool = True) -> None:
        """Say the one fixed line that ends a broken turn. Never raises:
        it is called from the failure path, and a mouth that just died is
        exactly the mouth being asked to say this."""
        if not speak:
            # Text-only caller (the CLI's one-shot mode): the turn still
            # needs its assistant row, but nothing may hit the speaker.
            self.history.append({"role": "assistant", "content": FAILURE_LINE})
            return
        try:
            await self._say_now(FAILURE_LINE)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("failure line could not be spoken")
        # _say_now records it as it speaks; if it died before doing so,
        # the turn would be left unanswered — which is the bug this
        # whole path exists to prevent.
        if not self.history or self.history[-1].get("content") != FAILURE_LINE:
            self.history.append({"role": "assistant", "content": FAILURE_LINE})

    async def _speak(self, sentence: str) -> None:
        import time as _time

        self.last_spoke_ts = _time.time()
        # The assistant just addressed the user; their reply is part of
        # this exchange and must not need the wake word again. Extends an
        # open window only — it cannot talk itself awake (wake.spoke).
        if self.wake is not None:
            try:
                self.wake.spoke()
            except Exception:
                logger.debug("wake.spoke failed (non-fatal)")
        chars = 0
        async for chunk in self.synthesise(sentence):
            if self._interrupted:
                break
            self.audio.play(chunk)
            chars = len(sentence)
        if chars and self.usage is not None:
            try:
                # to_thread: a sync sqlite write on the event loop can
                # stall every task for seconds under lock contention.
                await asyncio.to_thread(self.usage.tts, chars)
            except Exception:
                logger.debug("usage.tts failed (non-fatal)")

    async def _run_tool(self, call: dict) -> str:
        import json as _json

        name = call.get("name", "")
        try:
            arguments = _json.loads(call.get("arguments") or "{}")
        except Exception:
            arguments = {}
        logger.info("tool: %s(%s)", name, arguments)
        try:
            return await self.toolbox.execute(name, arguments)
        except Exception:
            logger.exception("tool %s failed", name)
            return "Не вийшло виконати дію."

    async def _drain_playback(self) -> None:
        while self.audio is not None and self.audio.playing:
            await asyncio.sleep(0.05)
