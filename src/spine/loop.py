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

# How long after a session closes an end phrase is still just the user
# pressing the button again.
END_PHRASE_GRACE_SECS = 90.0

_FINISHING_LINE = "Хвилинку, збираю підсумок."


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
    usage: Any = None          # .stt(seconds) / .tts(chars) (sync)
    # The role platform. All injected (this file imports no siblings):
    # roles is the loaded registry, role_manager owns the active session,
    # the two matchers recognise start/end phrases, save_artifact persists
    # the end-of-session document and returns its path.
    roles: dict = field(default_factory=dict)
    role_manager: Any = None   # .start/.finish/.cancel/.active/.note_turn
    trigger_match: Any = None  # (text, roles) -> Role | None
    end_match: Any = None      # (text, role) -> bool
    save_artifact: Any = None  # (role_name, md) -> str path
    hint_sink: Any = None      # async (text) -> None; hints-channel delivery
    _role_log: list = field(default_factory=list)  # session exchanges
    # Building an artifact is a whole LLM call over a whole session —
    # seconds, sometimes tens of them. The flag makes that wait visible
    # and keeps the conversation from answering in the meantime.
    role_finishing: bool = False
    _role_ended_ts: float = 0.0
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
    # Set when the user starts talking over the assistant (full duplex
    # only); respond() checks it between chunks and stops feeding the
    # speaker, while still collecting the full reply text for history.
    _interrupted: bool = False

    @property
    def _duplex(self) -> bool:
        """Full duplex only when a live canceller protects the mic."""
        return self.aec is not None and getattr(self.aec, "active", False)

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
            if self.aec is not None:
                frame = self.aec.process(frame)
            event = self.vad.feed(frame)
            if event is None:
                continue
            kind = getattr(event.kind, "value", event.kind)
            if kind == "start":
                self._starts_seen += 1
                self.assembler.speech_started()
                if self.audio.playing and self._duplex:
                    # Barge-in: the user talks over the assistant. Drop
                    # what is queued and tell respond() to stop feeding.
                    self._interrupted = True
                    dropped = self.audio.stop_playback()
                    # The dropped bytes are already in the AEC reference
                    # but will never leave the speaker — without this the
                    # canceller subtracts a phantom echo from exactly the
                    # utterance the user is interrupting with.
                    self.aec.clear()
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
            in_role = (
                self.role_manager is not None and self.role_manager.active
            )
            if gated and not in_role and (
                self.wake is not None and not self.wake.accepts(turn)
            ):
                # An active role session hears everything (a meeting
                # secretary that ignored the unaddressed room would have
                # nothing to summarise); outside a session the wake gate
                # rules as usual.
                logger.info("asleep — turn not addressed to me: %r", turn[:60])
                continue
            try:
                if await self._role_turn(turn):
                    continue
                await self.respond(turn)
            except Exception:
                logger.exception("turn failed: %r", turn[:80])

    async def _role_turn(self, turn: str) -> bool:
        """Role lifecycle and the log channel. True when the turn is
        consumed here and must not go to the normal respond() path."""
        if self.role_manager is None:
            return False
        active = self.role_manager.active

        if self.role_finishing:
            # The summary is still being written; anything said now would
            # be answered by a conversation that is already over.
            logger.info("role finishing — turn held: %r", turn[:60])
            return True

        if active is not None and self.end_match is not None and self.end_match(
            turn, active
        ):
            # Off the turn loop: an 18-second summary must not look like
            # a dead button, and repeated «закінчили» must not queue up.
            self.role_finishing = True
            asyncio.create_task(self._role_finish())
            return True

        if active is None and self._recently_ended(turn):
            logger.info("stray end phrase ignored: %r", turn[:60])
            return True

        if active is None and self.trigger_match is not None:
            role = self.trigger_match(turn, self.roles)
            if role is not None:
                ack = self.role_manager.start(role)
                self._role_log.clear()
                if getattr(role, "channel", "voice") == "log" and self.audio:
                    self.audio.mute_output = True
                await self._say_now(ack)
                return True

        channel = getattr(active, "channel", "voice") if active else "voice"
        if active is not None and channel in ("log", "hints"):
            # The quiet channels: everything is recorded, nothing is
            # spoken until the session ends. "hints" additionally turns
            # each heard turn into a silent prompt for the dashboard —
            # the interview prompter.
            self._role_log.append({"user": turn, "agent": None})
            if self.persist is not None:
                try:
                    turn_id = await asyncio.to_thread(
                        self.persist.log_user_turn, turn
                    )
                    self.role_manager.note_turn(turn_id)
                except Exception:
                    logger.exception("role persist failed (non-fatal)")
            if channel == "hints" and self.hint_sink is not None:
                # Off the consuming path: a hint is useless if waiting
                # for it delays hearing the next question.
                asyncio.create_task(self._make_hint(active, turn))
            return True

        return False

    async def _make_hint(self, role: Any, turn: str) -> None:
        try:
            recent = [e["user"] for e in self._role_log[-6:-1]]
            context = "\n".join(f"- {t}" for t in recent)
            messages = [
                {
                    "role": "system",
                    "content": (
                        f"{getattr(role, 'prompt', '')}\n\n"
                        "Відповідай ТІЛЬКИ підказкою: 3-5 коротких пунктів "
                        "плану відповіді, без вступу і пояснень."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        (f"Перед цим звучало:\n{context}\n\n" if context else "")
                        + f"Щойно прозвучало: {turn}"
                    ),
                },
            ]
            parts: list[str] = []
            async for delta in self.stream_chat(messages):
                parts.append(delta)
            hint = "".join(parts).strip()
            logger.info("hint for %r: %d chars", turn[:40], len(hint))
            if hint:
                # The heard question travels with the plan: on screen the
                # user must see what it is a plan *for*.
                await self.hint_sink(hint, turn)
        except Exception:
            logger.exception("hint generation failed (non-fatal)")

    def _recently_ended(self, turn: str) -> bool:
        """An end phrase repeated just after a session closed is a person
        pressing the button again, not a new thing to discuss."""
        import time as _time

        if _time.time() - self._role_ended_ts > END_PHRASE_GRACE_SECS:
            return False
        if self.end_match is None:
            return False
        return any(self.end_match(turn, r) for r in self.roles.values())

    async def _role_finish(self) -> None:
        active = self.role_manager.active
        if self.audio is not None:
            self.audio.mute_output = False

        # Building an artifact takes one whole LLM call over a whole
        # session — measured 7 to 30 seconds. Said out loud, that is a
        # pause; unsaid, it is the assistant having died.
        if self._role_log:
            await self._say_now(_FINISHING_LINE)

        async def _complete(messages: list[dict]) -> str:
            parts: list[str] = []
            async for delta in self.stream_chat(messages):
                parts.append(delta)
            return "".join(parts)

        import time as _time

        try:
            artifact = await self.role_manager.finish(
                exchanges=list(self._role_log), complete=_complete
            )
        except Exception:
            logger.exception("role finish failed")
            artifact = None
        finally:
            self._role_log.clear()
            self.role_finishing = False
            self._role_ended_ts = _time.time()

        if artifact is None:
            await self._say_now("Роль завершено.")
            return
        path = ""
        if self.save_artifact is not None and artifact.full_md.strip():
            try:
                path = self.save_artifact(
                    getattr(active, "name", "роль"), artifact.full_md
                )
            except Exception:
                logger.exception("artifact save failed")
        logger.info("role artifact: %s (%d chars)", path, len(artifact.full_md))
        logger.info("say (summary): %r", artifact.spoken[:80])
        await self._say_now(artifact.spoken)

    async def _say_now(self, text: str) -> None:
        """Speak a short service phrase outside the LLM flow."""
        if not text:
            return
        logger.info("say (service): %r", text[:70])
        self.history.append({"role": "assistant", "content": text})
        if self.audio is None:
            return
        muted = self.audio.mute_output
        self.audio.mute_output = False
        try:
            await self._speak(text)
            try:
                await asyncio.wait_for(self._drain_playback(), timeout=30.0)
            except asyncio.TimeoutError:
                self.audio.stop_playback()
        finally:
            self.audio.mute_output = muted

    async def respond(self, user_text: str, *, speak: bool = True) -> str:
        """One full exchange. Returns the reply text (also spoken)."""
        self._interrupted = False
        turn_id: int | None = None
        if self.persist is not None:
            try:
                turn_id = await asyncio.to_thread(
                    self.persist.log_user_turn, user_text
                )
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
        try:
            async for sentence in self.split_sentences(_deltas()):
                logger.info("say: %r", sentence)
                if speaking and not self._interrupted:
                    await self._speak(sentence)

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
                        await self._speak(spoken)

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

        reply = "".join(parts).strip()
        if reply:
            self.history.append({"role": "assistant", "content": reply})
            if self.persist is not None and turn_id is not None:
                try:
                    await asyncio.to_thread(
                        self.persist.log_agent_reply, reply, turn_id
                    )
                except Exception:
                    logger.exception("persist failed (non-fatal)")
        if self.role_manager is not None and self.role_manager.active:
            # A voice-channel session (teacher, interviewer) keeps its own
            # transcript for the end-of-session artifact.
            self.role_manager.note_turn(turn_id)
            self._role_log.append({"user": user_text, "agent": reply or None})
        return reply

    async def _speak(self, sentence: str) -> None:
        import time as _time

        self.last_spoke_ts = _time.time()
        chars = 0
        async for chunk in self.synthesise(sentence):
            if self._interrupted:
                break
            if self.aec is not None:
                self.aec.push_far(chunk)
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
