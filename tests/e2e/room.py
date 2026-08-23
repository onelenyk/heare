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

* **the ear**, because a test types instead of speaking;
* **the mouth**, because there is no audio device (with `audio=None`
  nothing is ever synthesised, so this costs no code at all);
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
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

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
) -> Room:
    """Assemble the assistant the way the daemon does, minus the world.

    `_build_loop` is the composition root the daemon calls — using it
    rather than hand-wiring is the whole point: a collaborator nobody
    connects is precisely the failure this layer exists to catch, and
    hand-wiring one here would hide it.
    """
    from src.config import load_settings
    from src.spine.main import _build_loop

    settings = load_settings()
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
    if features:
        settings.spine_features = dict(features)

    # Patched on the module, before anything is wired — because the model
    # is reached by four different roads and only two of them go through
    # the conductor. The summariser, the model's veto on speaking first,
    # and the repeats pass each hold their own reference, taken at wiring
    # time. Replacing `loop.stream_chat` alone leaves three of them
    # talking to the network: the first run of this harness proved it by
    # getting a 401 out of a test that was not supposed to have a wire.
    import src.spine.llm as llm

    room = Room(db=settings.db_path, loop=None)
    room._real = (llm.stream_chat, llm.stream_chat_events)

    def _plain(messages, _cfg=None, **_kw) -> AsyncIterator[str]:
        async def stream() -> AsyncIterator[str]:
            async for event in _speak(room, messages):
                if event["type"] == "delta":
                    yield event["text"]

        return stream()

    def _events(messages, _cfg=None, *, tools=None, **_kw) -> AsyncIterator[dict]:
        return _speak(room, messages)

    llm.stream_chat = _plain
    llm.stream_chat_events = _events

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
        settings, audio=None, voice="", hold_s=0.0, full=True, state=state,
        without=without
    )
    room.loop = loop
    # The keyboard is outside, like the model: read from the real machine
    # a test would pass or fail depending on whether anyone happened to
    # move the mouse. Default is "just now" — someone is at the desk.
    if loop.engine is not None:
        loop.engine._idle = room._idle
    room._task = asyncio.ensure_future(loop._converse())
    return room


async def _speak(room: Room, messages: list[dict]) -> AsyncIterator[dict]:
    """The scripted model.

    Records what it was asked, then answers whatever was queued. An empty
    queue answers with silence rather than raising: a test that provokes
    an unexpected extra turn should fail on what the assistant did, not
    on the harness running out of lines.
    """
    room._asked.append(messages)
    answer = room._script.pop(0) if room._script else Says()
    for word in answer.text.split(" "):
        yield {"type": "delta", "text": word + " "}
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
    if room._task is not None:
        room._task.cancel()
        try:
            await room._task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    from src.spine.main import _close_loop

    try:
        await _close_loop(room.loop)
    except Exception:  # noqa: BLE001
        pass


__all__ = ["Room", "Says", "close_room", "open_room"]
