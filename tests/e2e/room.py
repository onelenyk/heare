"""The whole assistant, assembled, driven, and asked what it did.

There were two kinds of test here and a hole between them. The unit
tests replace every collaborator with a fake, so they prove a rule and
never a path. `test_spine_golden.py` drives the real wiring against real
Groq and DeepSeek — true, slow, and it costs money, so it runs when
somebody remembers to run it.

Every bug found by hand on 22 August lived in that hole. The search verb
worked perfectly when called directly and answered with rubbish in a
conversation. The guard against interrupting could not fire because an
object was never wired. A question landed in the database before the
tool that searched it. None of those is a rule being wrong; each is one
part handing something to the next part.

So: the real loop, the real toolbox, the real engine, the real database,
and exactly three things replaced at the edge —

* **the ear** — the recogniser only. Not the microphone path around it:
  the loudness gate that decides whether Groq is paid at all, and the
  filter that decides whether what came back was ever speech, are both
  real here, because both have turned an evening bad and neither was on
  a test path until 23 August;
* **the mouth** — the synthesiser only. There was no mouth at all for
  the first thirty-eight scenarios, on the reasoning that `audio=None`
  costs no code. It cost three things that live only in the speaking
  branch: the stamp the junk filter reads, the extension of the wake
  window, and the choice of voice — and a wrong voice is silence, which
  is this project's worst failure shape. The Utterance object now tracks
  each reply's state: `.stopped`, `.paused`, `.outcome` (done/barged/cancelled/
  wedged). `room.outcome()` reads the latest utterance's outcome.
* **the model**, which is scripted, because a test that cannot say what
  the model answers is not testing anything downstream of it.

Everything between those three is the thing under test.

Two properties make it worth writing
------------------------------------
**The clock is an argument.** `Situation`, `judge` and every engine pass
take `now`, so "thirty minutes later" is a number rather than a wait.
That is what makes conversation boundaries, night, trust decay and the
week-long retention of overheard speech testable at all.

**What it says is read from the database.** Not from the log: tool
acknowledgements are spoken without a `say:` line, so a test reading the
log sees a turn that broke off where there was in fact an answer. That
mistake cost half an hour by hand; it is written down here so it cannot
cost it again.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

# How long to wait for a turn to finish before calling it wedged. Real
# turns here are milliseconds — the model is a list — so anything near
# this means something is actually stuck.
TURN_TIMEOUT_S = 10.0


@dataclass
class Says:
    """One programmed answer from the model.

    `text` is spoken; `calls` are tool calls the loop will run afterwards,
    each `(name, arguments)`. Both may be present: the model that says
    "let me look" and then looks is the shape that produced the worst
    behaviour observed live.
    """

    text: str = ""
    calls: tuple[tuple[str, dict], ...] = ()
    # The model falling over mid-answer: whatever `text` it managed is
    # streamed first, then this is raised. A broken turn must still end
    # with a sound, and this is how a test asks for one.
    fails: str = ""


@dataclass
class Room:
    """The assembled assistant, and the questions worth asking it."""

    db: Path
    loop: Any = None
    _script: list[Says] = field(default_factory=list)
    _asked: list[list[dict]] = field(default_factory=list)
    _task: Any = None
    _real: tuple = ()
    state: Any = None
    # The door: what the recogniser will return next, what it was
    # actually asked about, and what came out the other side.
    _next_heard: str = ""
    _real_stt: Any = None
    _real_tts: Any = None
    recognised: list = field(default_factory=list)
    through: list = field(default_factory=list)
    voiced: list = field(default_factory=list)
    _tasks: list = field(default_factory=list)
    # How long one synthesised sentence sounds for, in milliseconds, and
    # how loud. Silent by default: a test that only needs the mouth to
    # have been used should not pay to generate a voice.
    _speech_ms: float = 10.0
    _speech_amp: float = 0.0
    # The synthesiser answering with no audio at all — what edge-tts does
    # on NoAudioReceived, which `tts.py` swallows. Set it and every
    # sentence comes back empty.
    silent_voice: bool = False
    # The settings object the assistant was built with and still reads,
    # so a test can change one mid-run and see whether it takes; and the
    # config each model call was made with, to see which one did.
    settings: Any = None
    configs: list = field(default_factory=list)

    # -- what the model will answer -----------------------------------

    def will_say(self, *answers: Says | str) -> None:
        """Queue answers, one per turn. A bare string is a plain reply."""
        self._script.extend(
            a if isinstance(a, Says) else Says(text=a) for a in answers
        )

    @property
    def prompts(self) -> list[list[dict]]:
        """Every message list the model was handed, in order.

        The system prompt is where the engine puts what is outstanding
        between the two of you, so a test can assert on what the
        assistant *knew* as well as on what it said.
        """
        return self._asked

    # -- driving it ---------------------------------------------------

    async def overhears(self, text: str) -> None:
        """Said in the room, with no reply expected.

        The gate is supposed to turn this away, so waiting the full turn
        timeout for an answer that must not come would spend ten seconds
        proving the test's own premise.

        Note that this consumes no queued answer: nothing reaches the
        model. Queueing one before calling this shifts the whole script
        by one, and the next real turn gets the wrong line.
        """
        before = len(self.rows())
        # Speech starts, then it is transcribed. The microphone path
        # always does both — the VAD opens the turn and the recogniser
        # fills it — and the assembler holds a fragment that arrived
        # without an opening, defending against a recogniser that died
        # mid-utterance.
        self.loop.assembler.speech_started()
        self.loop.assembler.transcript(text)
        # Returns the moment something is written down, and gives up
        # quickly when nothing is — because "nothing was written down" is
        # what most of these cases are asserting, and waiting the full
        # turn timeout for it would spend ten seconds proving the test's
        # own premise.
        deadline = asyncio.get_running_loop().time() + 1.0
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
            if len(self.rows()) != before:
                return

    async def hears(self, text: str) -> str:
        """Something said in the room, through the wake gate.

        This is the path a microphone takes. Whether it becomes a turn at
        all is the gate's decision, which is exactly what some of these
        tests are about.
        """
        before = self._last_agent_row()
        self.loop.assembler.speech_started()
        self.loop.assembler.transcript(text)
        return await self._settle(before)

    async def spoken(self, text: str, *, ms: float = 600.0,
                     quiet: bool = False) -> str:
        """Said into the microphone — including the part before the loop.

        `hears()` hands text straight to the assembler, which is the
        right entrance for everything downstream but skips a stretch of
        real code: the loudness gate that decides whether to pay for
        recognition at all, and the filter that decides whether what came
        back was ever speech. Both live in the composition root, both
        have turned an evening bad, and neither was on any test path.

        Returns what survived that stretch — empty when it was turned
        away, in which case nothing downstream ever saw it.
        """
        self._next_heard = text
        pcm = _audio(ms, quiet=quiet)
        self.loop.assembler.speech_started()
        await self.loop._transcribe(pcm, self.loop._starts_seen)
        return self.through[-1] if self.through else ""

    # -- the microphone, and talking over it --------------------------

    @property
    def mouth(self) -> Any:
        """The speaker, for tests that ask what is still coming out of it."""
        return self.loop.audio

    def speaks_for(self, ms: float) -> None:
        """How long each synthesised sentence sounds for.

        Ten milliseconds by default, which is why every scenario before
        this one finished speaking before the next line of the test ran.
        A test about interruption has to set this: the window in which
        barge-in is even possible *is* the length of the reply.
        """
        self._speech_ms = ms

    def speaks_aloud(self, amp: float = 9000.0) -> None:
        """Render an actual voice instead of silence.

        Only one kind of test needs this, and it is the kind that cannot
        be faked: a room where the microphone hears the assistant coming
        back. It is a knob here rather than a patch in the test, because
        the synthesiser is one of the three edges this harness replaces
        *before* the loop is wired — `loop.synthesise` holds the closure
        from that moment, and a test that re-patches `src.spine.tts`
        afterwards changes nothing and gets silence while believing it
        has a voice. That mistake cost an hour and two scenarios that
        passed for the wrong reason.
        """
        self._speech_amp = amp

    async def until_speaking(self, timeout: float = 5.0) -> bool:
        """Wait until audio is actually coming out of the speaker."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if self.mouth.playing:
                return True
            await asyncio.sleep(0.005)
        return False

    async def into_the_mic(self, text: str, *, ms: float = 400.0,
                          then_quiet_ms: float = 900.0) -> None:
        """Speech as the device delivers it: 20 ms frames, one by one.

        Everything else here hands the assembler a finished sentence.
        That is the right entrance for a test about what the assistant
        does with words, and the wrong one for a test about whether it
        hears them at all — the canceller, the energy detector and the
        decision to stop talking all live between the device and the
        assembler, and none of them had ever run in this harness.

        The quiet tail is not padding: the detector ends an utterance on
        silence, and without it the words stay in flight forever.
        """
        self._next_heard = text
        frame_bytes = int(16000 * 0.020) * 2
        loud = _audio(ms)
        quiet = _audio(then_quiet_ms, quiet=True)
        for stream in (loud, quiet):
            for at in range(0, len(stream) - frame_bytes + 1, frame_bytes):
                await self.mouth.input_frames.put(stream[at:at + frame_bytes])
                # Let the ear run: the queue is not the point, the frames
                # reaching the detector one at a time is.
                await asyncio.sleep(0)

    async def frames(self, pcm: bytes) -> None:
        """Raw microphone audio, delivered 20 ms at a time.

        Below `into_the_mic`, for the one thing that has to build its own
        frames: a room where the microphone hears the assistant's own
        voice coming back as well as the person's.
        """
        frame_bytes = int(16000 * 0.020) * 2
        for at in range(0, len(pcm) - frame_bytes + 1, frame_bytes):
            await self.mouth.input_frames.put(pcm[at:at + frame_bytes])
            await asyncio.sleep(0)

    def cost_events(self) -> list[tuple]:
        """What was billed. Silence that reaches Groq is money."""
        with sqlite3.connect(self.db) as db:
            return db.execute(
                "SELECT kind, audio_seconds FROM usage_events ORDER BY id"
            ).fetchall()

    async def said_to_it(self, text: str) -> None:
        """Addressed to it, with no spoken reply expected.

        A role trigger and its end phrase are answered with a service
        phrase, which is spoken outside the model flow and leaves no row.
        Waiting for one costs the full turn timeout and proves nothing.
        """
        await self.loop.inject(text)
        await self.drained()

    async def told(self, text: str) -> str:
        """Something addressed to it, bypassing the gate.

        The injection queue: how the dashboard, a delegated job's answer
        and these tests all reach the assistant. Already addressed by
        construction, so the gate is not consulted.
        """
        before = self._last_agent_row()
        await self.loop.inject(text)
        return await self._settle(before)

    async def _settle(self, before: int) -> str:
        """Wait for the turn to land in the database, or give up.

        Reading the reply from `transcripts` rather than from a return
        value is deliberate: it is the same thing a person could check
        afterwards, and it is the only place a tool's spoken
        acknowledgement appears.
        """
        deadline = asyncio.get_running_loop().time() + TURN_TIMEOUT_S
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.02)
            latest = self._last_agent_row()
            if latest != before:
                return self.said()
        return ""

    def mark(self) -> int:
        """A bookmark in what it has said, to wait past later.

        `drained()` is the wrong instrument when the next turn has not
        started yet: it watches for nothing *changing*, and a turn still
        travelling from the recogniser to the conductor changes nothing
        for a moment. Waiting for the row itself is the honest wait.
        """
        return self._last_agent_row()

    async def answer_after(self, mark: int) -> str:
        """The next thing it says after that bookmark. Empty if it never
        says anything, which is a verdict some of these tests want."""
        return await self._settle(mark)

    async def drained(self) -> None:
        """Wait until nothing is in flight.

        The engine speaks by injecting, so its own remark is a queued
        turn like any other. A test that ticks and then immediately says
        something is racing two turns through one queue, and will read
        the reply to the wrong one.
        """
        deadline = asyncio.get_running_loop().time() + TURN_TIMEOUT_S
        settled = self._last_agent_row()
        still = 0
        while asyncio.get_running_loop().time() < deadline and still < 8:
            await asyncio.sleep(0.03)
            latest = self._last_agent_row()
            if latest == settled and self.loop._injected.empty():
                still += 1
            else:
                settled, still = latest, 0

    async def tick(self, now: float) -> Any:
        """One engine pass at a stated moment.

        The clock is an argument all the way down, so a test says when it
        is instead of sleeping until then.
        """
        return await self.loop.engine.tick(now=now)

    _idle_s: float = 2.0

    async def _idle(self) -> float:
        return self._idle_s

    def away_for(self, seconds: float) -> None:
        """How long since anyone touched the keyboard.

        Presence is either "talked lately" or "at the desk"; a test about
        an empty room has to be able to say both are false.
        """
        self._idle_s = seconds

    def is_talking(self, talking: bool = True) -> None:
        """Stamp what the assistant is doing, the way the daemon does.

        `agent_state` is written by the daemon's speaking wrapper, which
        this layer does not run — so the test writes it. What is being
        checked is the wire underneath: for months the reader looked for
        words no writer produced, and the guard against speaking into the
        middle of a sentence therefore never fired once.
        """
        import json
        import time

        self.state.set_cache_only(
            "agent_state",
            json.dumps({"state": "talking" if talking else "idle",
                        "since_ts": time.time()}),
        )

    def remembers(self, text: str, *, days_ago: float = 1.0, agent: int = 0) -> None:
        """Something said before this test began.

        Written straight to the table rather than through a turn: these
        are the months already on disk, and replaying them as
        conversations would take longer than the thing being tested and
        prove nothing about it.
        """
        import time

        with sqlite3.connect(self.db) as db:
            db.execute(
                "INSERT INTO transcripts (ts, text, mode, agent_spoken, source) "
                "VALUES (?, ?, 'spine', ?, 'voice')",
                (time.time() - days_ago * 86400, text, agent),
            )
            db.commit()

    async def cuts_in(self, text: str, *, ms: float = 400.0) -> bool:
        """Start talking while the assistant is still speaking.

        Returns whether the interruption landed — which is the single
        number the old measurement of this reported (two runs in four,
        against an engine that no longer exists) and the one nothing has
        measured since. Read off the utterance now, not a flag.
        """
        assert self.mouth.playing, "nothing to interrupt — the mouth is idle"
        await self.into_the_mic(text, ms=ms)
        await self._decided()
        return self.loop.interrupted

    async def _decided(self, timeout: float = 2.0) -> None:
        """Wait for the verdict on a voice over the speaker.

        The speaker pauses on the spot; whether that was a nod or an
        interruption is known only once the transcript is back, on the
        STT worker, after `into_the_mic` has returned. A test that reads
        the outcome before that reads the pause.
        """
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if getattr(self.loop, "_hold", None) is None and self.loop._stt_jobs.empty():
                return
            await asyncio.sleep(0.005)

    async def nods(self, text: str = "ага", *, ms: float = 250.0) -> None:
        """A short sound into the microphone while it is speaking.

        Same path as `cuts_in` — frames, the detector, the canceller —
        only shorter. Whether that pauses, stops or is waved through is
        the two-stage decision of docs/archive/2026-09-16/design.html §6.
        """
        assert self.mouth.playing, "nothing to nod at — the mouth is idle"
        await self.into_the_mic(text, ms=ms)
        await self._decided()

    async def finished(self, utterance: Any, timeout: float = 6.0) -> str | None:
        """Wait for one utterance to end, and say how.

        `drained()` watches the database and the inject queue — the
        conversation. This watches the speaker: a reply resumed after a
        nod is still sounding long after the row for it was written.
        """
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if utterance.outcome is not None:
                return utterance.outcome
            await asyncio.sleep(0.02)
        return utterance.outcome

    def paused(self) -> bool:
        """Whether the latest utterance is paused.

        Returns True if there is a mouth and a last utterance that is
        paused, else False.
        """
        if hasattr(self.loop, "mouth") and self.loop.mouth is not None:
            mouth = self.loop.mouth
            if mouth.last is not None:
                return mouth.last.paused
        return False

    def outcome(self) -> str | None:
        """How the latest utterance ended: 'done', 'barged', 'cancelled', or None."""
        if hasattr(self.loop, "mouth") and self.loop.mouth is not None:
            mouth = self.loop.mouth
            if mouth.last is not None:
                return mouth.last.outcome
        return None

    # -- what it did --------------------------------------------------

    def said(self, n: int = 1) -> str:
        """The last thing the assistant said. Empty if it never spoke."""
        rows = self.rows("agent_spoken = 1")
        return rows[-n][2] if len(rows) >= n else ""

    def heard(self) -> list[str]:
        """Everything written down as the person's, oldest first."""
        return [text for _ts, _agent, text, _src in self.rows("agent_spoken = 0")]

    def rows(self, where: str = "1=1") -> list[tuple]:
        with sqlite3.connect(self.db) as db:
            return db.execute(
                "SELECT ts, agent_spoken, text, source FROM transcripts "
                f"WHERE {where} ORDER BY ts, id"
            ).fetchall()

    def conversations(self) -> list[tuple]:
        with sqlite3.connect(self.db) as db:
            return db.execute(
                "SELECT id, start_ts, end_ts, summary FROM conversations "
                "ORDER BY id"
            ).fetchall()

    def intents(self, state: str | None = None) -> list[tuple]:
        clause = f"WHERE state = '{state}'" if state else ""
        with sqlite3.connect(self.db) as db:
            return db.execute(
                f"SELECT kind, text, urgency, state FROM intents {clause} "
                "ORDER BY id"
            ).fetchall()

    def _last_agent_row(self) -> int:
        with sqlite3.connect(self.db) as db:
            row = db.execute(
                "SELECT COALESCE(MAX(id), 0) FROM transcripts WHERE agent_spoken = 1"
            ).fetchone()
        return int(row[0])


async def open_room(
    tmp_path: Path,
    *,
    without: str = "",
    features: dict | None = None,
    settings: dict | None = None,
) -> Room:
    """Assemble the assistant the way the daemon does, minus the world.

    `_build_loop` is the composition root the daemon calls — using it
    rather than hand-wiring is the whole point: a collaborator nobody
    connects is precisely the failure this layer exists to catch, and
    hand-wiring one here would hide it.
    """
    from src.config import load_settings
    from src.spine.main import _build_loop

    overrides, settings = settings, load_settings()
    settings.db_path = tmp_path / "heare.db"
    # Nothing here may reach the network. The keys are set so that
    # resolution succeeds and the scripted model replaces the real one
    # below; they are never used to open a connection.
    settings.groq_api_key = settings.groq_api_key or "test"
    settings.deepseek_api_key = settings.deepseek_api_key or "test"
    settings.llm_provider = "deepseek"
    # The turn clock, wound right down. In the room these hold a turn
    # open in case the person is still speaking; here every fragment is
    # a whole sentence delivered at once, and the wait is the difference
    # between a suite that runs in seconds and one nobody runs.
    settings.spine_turn_hold_seconds = 0.0
    settings.spine_turn_continuation_hold_seconds = 0.0
    # Always set, never inherited. `load_settings()` reads the developer's
    # own ~/.heare/config.toml, so without this line a switch turned on
    # there — `hear_all`, on the afternoon this was written — silently
    # changes what the suite is testing, and two scenarios about the room
    # leaving nothing behind go red on a machine that is merely
    # configured differently.
    settings.spine_features = dict(features or {})
    # Last, so a test can put back what the room winds down — the turn
    # hold, for one — and say so in its own arguments.
    for name, value in (overrides or {}).items():
        setattr(settings, name, value)

    # Patched on the module, before anything is wired — because the model
    # is reached by four different roads and only two of them go through
    # the conductor. The summariser, the model's veto on speaking first,
    # and the repeats pass each hold their own reference, taken at wiring
    # time. Replacing `loop.stream_chat` alone leaves three of them
    # talking to the network: the first run of this harness proved it by
    # getting a 401 out of a test that was not supposed to have a wire.
    import src.spine.llm as llm

    room = Room(db=settings.db_path, loop=None, settings=settings)
    room._real = (llm.stream_chat, llm.stream_chat_events)

    def _plain(messages, _cfg=None, **_kw) -> AsyncIterator[str]:
        async def stream() -> AsyncIterator[str]:
            async for event in _speak(room, messages):
                if event["type"] == "delta":
                    yield event["text"]

        return stream()

    def _events(messages, _cfg=None, *, tools=None, **_kw) -> AsyncIterator[dict]:
        room.configs.append(_cfg)
        return _speak(room, messages)

    llm.stream_chat = _plain
    llm.stream_chat_events = _events

    # The recogniser, patched the same way and for the same reason: the
    # composition root takes its own reference at wiring time. What is
    # under test here is not Whisper but everything the root does around
    # it — skip the call when the audio is too quiet to be a word, and
    # throw away what came back when it is a known hallucination.
    import src.spine.stt as stt

    room._real_stt = stt.transcribe

    async def _recognise(pcm, **_kw):
        room.recognised.append(len(pcm))
        return stt.Transcript(text=room._next_heard, language="uk")

    stt.transcribe = _recognise

    # The mouth, for the same reason again — and this one is also the
    # only way to see which voice was chosen, which is a decision made
    # per reply and never yet observed by a test.
    import src.spine.tts as tts

    room._real_tts = tts.synthesise

    def _voice(text: str, *, voice: str = "", **_kw):
        async def stream():
            room.voiced.append((text, voice))
            if room.silent_voice:
                return
            # Ten milliseconds by default — enough for the mouth to have
            # been used, short enough that fifty scenarios do not wait on
            # a speaker. A test about being talked over asks for a real
            # sentence's worth instead (`room.speaks_for`), because you
            # cannot interrupt something that is already finished.
            frames = int(24000 * room._speech_ms / 1000.0)
            if not room._speech_amp:
                yield b"\x00\x00" * frames
                return
            yield pack(voice_like(frames, 24000, room._speech_amp))

        return stream()

    tts.synthesise = _voice

    # The real State, because the engine's view of the present is built
    # from it and the whole point of this layer is that a collaborator
    # nobody connects is the failure it exists to catch. In production
    # the daemon owns this object and stamps `agent_state` on every
    # utterance; here the test stamps it, through `room.is_talking()`.
    # The schema first: `State.init()` reads tables that
    # `SpinePersistence`'s constructor creates, and in the daemon the
    # store is always open before the state is asked anything.
    from src.spine.persist import SpinePersistence
    from src.state import State

    SpinePersistence(settings.db_path).close()
    state = State(settings.db_path)
    await state.init()
    room.state = state

    loop = await _build_loop(
        settings, audio=Mouth(), voice="", hold_s=0.0, full=True, state=state,
        without=without
    )
    room.loop = loop
    # Watch what the door lets through, without changing it. The gate
    # and the filter live inside a closure in the composition root; this
    # is the only place their verdict is visible from outside.
    _door = loop.transcribe

    async def _watched(pcm):
        result = await _door(pcm)
        room.through.append((getattr(result, "text", None) or "").strip())
        return result

    loop.transcribe = _watched
    # The keyboard is outside, like the model: read from the real machine
    # a test would pass or fail depending on whether anyone happened to
    # move the mouse. Default is "just now" — someone is at the desk.
    if loop.engine is not None:
        loop.engine._idle = room._idle
    # The ear, the recogniser's queue and the conductor — the three tasks
    # `loop.run()` starts. Only the last one ran here until barge-in
    # needed a test: everything reached the assembler by hand, so the VAD
    # never segmented anything and `_listen` — where the decision to
    # interrupt is actually made — was not on any path at all.
    room._tasks = [
        asyncio.ensure_future(loop._listen()),
        asyncio.ensure_future(loop._stt_worker()),
        asyncio.ensure_future(loop._converse()),
    ]
    room._task = room._tasks[-1]
    return room


class Mouth:
    """A speaker that keeps what it was given instead of playing it.

    This harness ran without one for its first thirty-eight scenarios,
    on the reasoning that `audio=None` costs no code. It does cost code:
    a whole branch of the loop is skipped, and three things that live
    only in that branch — the stamp that tells the junk filter the
    assistant just spoke, the extension of the wake window, and the
    choice of voice for the reply — were therefore never on any path.
    The last one has a standing suspicion against it: Edge TTS renders
    Cyrillic on an English voice as silence.

    Then it played nothing, and `playing` was hardcoded False. That one
    line was the whole reason barge-in had no coverage at this layer:
    the conductor asks `audio.playing and self._duplex` before it will
    interrupt, and the first half was a constant no. Everything else was
    already real here — the canceller included, active, wired as the
    far-end sink — so the branch that *decides* to stop the mouth was
    unreachable while the branch that obeys the decision had five tests.

    So the queue is honest now. Bytes leave at the output rate, which is
    a clock and not a wait: nothing sleeps, `playing` simply answers
    whether the audio handed over would still be coming out of a real
    speaker at this instant.
    """

    # 24 kHz mono int16 — what `play()` is handed, and the rate it would
    # leave a device at.
    BYTES_PER_SECOND = 24000 * 2

    def __init__(self) -> None:
        self.input_frames: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self.mute_input = False
        self.mute_output = False
        self.far_sink = None
        self.played: list[bytes] = []
        # When the queue would run dry. Before that instant the speaker
        # is still sounding; after it, silence.
        self._quiet_at = 0.0
        self.cleared_far_end = 0

    def _now(self) -> float:
        import time

        return time.monotonic()

    def take_input_gap(self) -> bool:
        return False

    def play(self, pcm: bytes) -> None:
        if self.mute_output:
            return
        self.played.append(pcm)
        self._quiet_at = max(self._quiet_at, self._now()) + (
            len(pcm) / self.BYTES_PER_SECOND
        )
        # The canceller only has something to subtract if it is told what
        # went to the speaker. The real audio layer does this in `play`;
        # a mouth that skips it hands the AEC an empty reference and every
        # echo test passes for the wrong reason.
        sink = self.far_sink
        push = getattr(sink, "push_far", None) if sink is not None else None
        if push is not None:
            try:
                push(pcm)
            except Exception:  # noqa: BLE001
                pass

    @property
    def playing(self) -> bool:
        return self._now() < self._quiet_at

    def queued_bytes(self) -> int:
        """What has not left the speaker yet."""
        left = self._quiet_at - self._now()
        return max(0, int(left * self.BYTES_PER_SECOND))

    def stop_playback(self) -> int:
        dropped = self.queued_bytes()
        self._quiet_at = self._now()
        if dropped:
            # Bytes that never reached the room must not be subtracted
            # from the voice that interrupted them. Counted, because this
            # is the half of barge-in nothing has ever checked.
            sink = self.far_sink
            clear = getattr(sink, "clear", None) if sink is not None else None
            if clear is not None:
                try:
                    clear()
                    self.cleared_far_end += 1
                except Exception:  # noqa: BLE001
                    pass
        return dropped


def voice_like(n: int, rate: int, amp: float) -> list[float]:
    """Something a canceller can be honestly measured against.

    Two formants over a wobbling pitch, not a tone. Speech is not
    stationary, and this project has already published one number taken
    against a tone and had to withdraw it: fed a tone the canceller
    returns 29, fed speech over an echo it returns the speech intact.
    """
    import math

    out = []
    for i in range(n):
        t = i / rate
        pitch = 130 + 25 * math.sin(2 * math.pi * 3.1 * t)
        v = (math.sin(2 * math.pi * pitch * t)
             + 0.6 * math.sin(2 * math.pi * 3 * pitch * t)
             + 0.3 * math.sin(2 * math.pi * 7 * pitch * t))
        env = 0.55 + 0.45 * math.sin(2 * math.pi * 4.7 * t)
        out.append(amp * env * v / 1.9)
    return out


def pack(samples) -> bytes:
    import struct

    return struct.pack(
        "<%dh" % len(samples),
        *[max(-32768, min(32767, int(s))) for s in samples],
    )


def _audio(ms: float, *, quiet: bool = False) -> bytes:
    """16 kHz mono int16, either plainly loud or plainly silent.

    Nothing here models a voice — the recogniser is scripted. What it
    has to be honest about is energy, because the gate in front of the
    recogniser is measured in milliseconds above a threshold, and a test
    that fakes that number tests nothing.
    """
    import struct

    frames = int(16000 * ms / 1000.0)
    if quiet:
        return b"\x00\x00" * frames
    # A square wave at half scale: about -6 dBFS, loud by any measure.
    return struct.pack("<%dh" % frames, *([16000, -16000] * (frames // 2)))


async def _speak(room: Room, messages: list[dict]) -> AsyncIterator[dict]:
    """The scripted model.

    Records what it was asked, then answers whatever was queued. An empty
    queue answers with silence rather than raising: a test that provokes
    an unexpected extra turn should fail on what the assistant did, not
    on the harness running out of lines.
    """
    room._asked.append(messages)
    answer = room._script.pop(0) if room._script else Says()
    for word in answer.text.split(" ") if answer.text else ():
        yield {"type": "delta", "text": word + " "}
    if answer.fails:
        raise RuntimeError(answer.fails)
    for name, arguments in answer.calls:
        import json

        yield {"type": "tool_call", "name": name, "arguments": json.dumps(arguments)}


async def close_room(room: Room) -> None:
    """Let go of the tasks and the file handles.

    An unclosed aiosqlite worker thread is non-daemon: without this a
    finished test hangs the interpreter with its output still buffered,
    which is a failure mode this project has already paid for once.
    """
    if room._real:
        import src.spine.llm as llm

        llm.stream_chat, llm.stream_chat_events = room._real
    if room._real_stt is not None:
        import src.spine.stt as stt

        stt.transcribe = room._real_stt
    if room._real_tts is not None:
        import src.spine.tts as tts

        tts.synthesise = room._real_tts
    for task in room._tasks or ([room._task] if room._task is not None else []):
        task.cancel()
    for task in room._tasks or ([room._task] if room._task is not None else []):
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    from src.spine.main import _close_loop

    try:
        await _close_loop(room.loop)
    except Exception:  # noqa: BLE001
        pass


def real_speech(text: str) -> bytes:
    """Generate 16 kHz mono int16 PCM of spoken text using macOS `say`.

    Uses `say` with a Ukrainian voice if available, falls back to any voice.
    Converts the output to PCM via ffmpeg.
    Raises `pytest.skip` if binaries are unavailable.
    """
    import subprocess

    # Check that required binaries exist
    try:
        subprocess.run(["which", "say"], check=True, capture_output=True)
        subprocess.run(["which", "ffmpeg"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        import pytest
        pytest.skip("say or ffmpeg not available")

    try:
        # Try to find a Ukrainian voice
        voices_output = subprocess.run(
            ["say", "-v", "?"],
            capture_output=True,
            text=True,
            timeout=5
        )
        voice = None
        for line in voices_output.stdout.split("\n"):
            if "uk_UA" in line or "ukrainian" in line.lower():
                voice = line.split()[0]
                break
        # Fall back to first available voice
        if voice is None:
            lines = [line for line in voices_output.stdout.split("\n") if line.strip()]
            if lines:
                voice = lines[0].split()[0]

        voice_arg = ["-v", voice] if voice else []

        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as handle:
            aiff_path = handle.name
        subprocess.run(
            ["say"] + voice_arg + ["-o", aiff_path, text],
            check=True,
            timeout=10
        )

        # Convert to 16 kHz mono int16 PCM
        pcm = subprocess.run(
            ["ffmpeg", "-i", aiff_path, "-ar", "16000", "-ac", "1",
             "-f", "s16le", "-"],
            capture_output=True,
            check=True,
            timeout=10
        )
        # A second of quiet after the word: the detector ends an utterance
        # on silence, and `say` stops the instant the word does.
        return pcm.stdout + b"\x00\x00" * 16000

    except subprocess.CalledProcessError as e:
        import pytest
        pytest.skip(f"real_speech failed: {e}")
    except Exception as e:
        import pytest
        pytest.skip(f"real_speech error: {e}")


__all__ = ["Mouth", "Room", "Says", "close_room", "open_room",
           "pack", "voice_like", "real_speech"]


# -- the hands, and waiting on them --------------------------------------


class Worker:
    """The hands' model, scripted — the worker's twin of `Says`.

    Only `Hands._chat` is replaced: the real toolbox starts the real
    worker, which builds its real prompt and tool list and hands both
    here. Each call is recorded, so a test can ask what the worker was
    shown; each answer is plain text, i.e. a finished job.
    """

    def __init__(self, *answers: str | dict) -> None:
        # A string is a finished job. A dict is the worker's model calling
        # tools: {"tool_calls": [("remind", {...}), ...]} — the real Hands
        # runs them and asks again, so script the next answer after it.
        self._answers = list(answers)
        self.calls: list[tuple[list[dict], list[dict]]] = []
        self.gate: asyncio.Event | None = None

    async def chat(self, messages: list[dict], schemas: list[dict]) -> dict:
        self.calls.append((messages, schemas))
        if self.gate is not None:
            # A job that takes as long as the test says: set `gate` to an
            # unset Event and the worker waits on it, still "running".
            await self.gate.wait()
        answer = self._answers.pop(0) if self._answers else "Готово."
        if isinstance(answer, dict):
            calls = [
                {
                    "id": f"call-{len(self.calls)}-{i}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                }
                for i, (name, args) in enumerate(answer["tool_calls"])
            ]
            return {"role": "assistant", "content": "", "tool_calls": calls}
        return {"role": "assistant", "content": answer}

    def offered(self, n: int = -1) -> set[str]:
        """Tool names the worker was shown on call `n`."""
        return {s["function"]["name"] for s in self.calls[n][1]}

    def context(self, n: int = -1) -> str:
        """Everything the worker was told besides the task, on call `n`."""
        return "\n".join(m["content"] for m in self.calls[n][0] if m["role"] == "system")

    def task(self, n: int = -1) -> str:
        return self.calls[n][0][-1]["content"]


def hire(monkeypatch: Any, *answers: str | dict) -> Worker:
    """Put a scripted worker behind `delegate` for the length of a test."""
    worker = Worker(*answers)

    async def _chat(_hands: Any, _client: Any, messages: list, schemas: list) -> dict:
        return await worker.chat(messages, schemas)

    monkeypatch.setattr("src.agent.hands.Hands._chat", _chat)
    return worker


async def until(check: Any, timeout: float = TURN_TIMEOUT_S) -> None:
    """Wait for `check()` to be true, or fail saying it never was."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not check():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("never happened")
        await asyncio.sleep(0.02)


def lines(room: Room) -> list[str]:
    """Everything the assistant said, oldest first, from the database."""
    return [text for _ts, agent, text, _src in room.rows() if agent]

