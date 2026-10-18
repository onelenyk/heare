"""The core: one conversation, and everything attached to it.

It works with messages, never with sound or sockets. It decides whose
turn it is, what interrupts what, that every message gets an answer,
that «стоп» stops everything, who is asked to do a piece of work and
whether they may. It reads and writes nothing itself: inputs bring
messages, outputs deliver replies, a model writes them, a memory keeps
them, executors do the work.

Two lanes. What the person says — on any channel — is taken at once and
ends whatever was being said. What anything else has to say — a result,
a question from an executor, a reminder — waits until nobody is talking.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
from typing import AsyncIterator, Awaitable, Callable

from heare.core.forms import (
    Delegator, Executor, Input, Memory, Message, Model, Output, Permission,
    Question, Task,
)
from heare.core.state import State

logger = logging.getLogger("heare.core")

DEFAULT_PROMPT = (
    "Ти асистент на ім'я heare. Відповідай коротко, українською, простою "
    "прозою — без розмітки, списків і коду. Одна-три фрази, як у живій розмові."
)

# Said when a turn dies below the core — the model, an output, anything.
# Silence is indistinguishable from "it did not hear me", so a broken turn
# still ends with words.
FAILURE_LINE = "Щось пішло не так — не зміг відповісти."

# Sent once, after a reply that promised work and called no verb.
KEEP_THE_PROMISE = (
    "You just told the person you would do that, but you called no tool, "
    "so nothing is happening. Call the tool you meant now. Write no text."
)

# An instruction, not a label: as a bare tag a small model read a result
# as more input and answered «зараз гляну» a second time.
RESULT_PREFIX = (
    "[результат роботи] Робота завершена. Ось що вийшло — перекажи це "
    "користувачу одним-двома реченнями, своїми словами. Не кажи, що ти "
    "щось перевіриш: це вже перевірено. Результат:"
)

# The word that means "I have nothing to say". The rule for when to say
# it is text beside the core (``persona.JUDGE``), handed in as ``judge``.
SILENCE = "МОВЧУ"
JUDGE = (
    "Наступну репліку сказано в кімнаті, не до тебе на ім'я. Якщо тобі є що "
    f"сказати по суті — відповідай коротко. Якщо ні — напиши рівно: {SILENCE}."
)

HISTORY_TURNS = 12
# How many recent turns an executor is shown: «відкрий той файл» names
# nothing without them.
CONTEXT_TURNS = 6
PERMISSION_TIMEOUT_S = 60.0
# An executor's question waits this long; the agent's own idle guard is 5 min.
QUESTION_TIMEOUT_S = 240.0
SAY_CHARS = 70


class NoWords:
    """The language the core has when it is handed none: nothing is a
    command and nothing is a promise. A real one (``heare.language.uk``)
    is given by whoever assembles the app; the core holds no words."""

    def is_halt(self, text: str, names: frozenset = frozenset()) -> bool:
        return False

    def consent(self, text: str, names: frozenset = frozenset()) -> bool | None:
        return None

    def decide(self, text: str, *, speech_ms: float) -> str:
        return "interrupt" if text.strip() else "resume"

    def promises_work(self, reply: str) -> bool:
        return False

    claims_done = promises_work

    def tokens(self, text: str) -> list[str]:
        return text.lower().split()


class Plain:
    """The delegator used until a model is given the job: the first
    executor, the request as it was said, the result as it came back."""

    async def assign(
        self, request: str, history: list[dict], executors: list[Executor]
    ) -> tuple[str, str]:
        talk = "\n".join(f"{m['role']}: {m['content']}" for m in history)
        task = f"{request}\n\nОстанні репліки розмови:\n{talk}" if talk else request
        return executors[0].name, task

    async def brief(self, task: Task) -> str:
        return task.result


async def _one(text: str) -> AsyncIterator[str]:
    yield text


class _Port:
    def __init__(self, core: Core) -> None:
        self._core = core

    def started(self, channel: str) -> None:
        self._core._started(channel)

    def heard(self, message: Message) -> None:
        self._core.hear(message)

    def nothing(self, channel: str) -> None:
        self._core._nothing(channel)

    def note(self, key: str, value: object) -> None:
        if self._core.state.around.get(key) != value:
            self._core.state.around[key] = value
            self._core.state.emit("around", key=key, value=value)

    def overheard(self, channel: str, text: str) -> None:
        self._core.state.emit("overheard", text=text, channel=channel)
        self._core._nothing(channel)

    def said_over(self, channel: str, text: str, speech_ms: float, long: bool) -> bool:
        return self._core._said_over(text, speech_ms, long)


class _Desk:
    def __init__(self, core: Core, task: Task) -> None:
        self._core, self._task = core, task

    def step(self, text: str) -> None:
        self._task.step = text
        self._core.state.emit("task", **self._task.view())

    async def permit(self, what: str, kind: str = "change") -> bool:
        return await self._core._ask(self._task, what, kind)

    async def question(self, text: str) -> str:
        return await self._core._question_of(self._task, text)


class Core:
    def __init__(
        self,
        model: Model,
        *,
        memory: Memory | None = None,
        delegator: Delegator | None = None,
        prompt: Callable[[], Awaitable[str]] | None = None,
        names: frozenset[str] = frozenset(),
        permission_timeout: float = PERMISSION_TIMEOUT_S,
        judge: str = JUDGE,
        words: object = None,
    ) -> None:
        """names: the assistant's own name in every spelling, lower case.
        It is not a word of a command: «Дока, стоп» is «стоп»."""
        self.model = model
        self.memory = memory
        self.delegator = delegator or Plain()
        self.names = names
        self.permission_timeout = permission_timeout
        self.judge = judge
        self.words = words or NoWords()
        self.state = State()
        self.port = _Port(self)
        self.history: list[dict] = []
        self.inputs: dict[str, Input] = {}
        self.outputs: dict[str, Output] = {}
        self.executors: dict[str, Executor] = {}
        self._prompt = prompt
        self._person: asyncio.Queue[Message] = asyncio.Queue()
        self._quiet: asyncio.Queue[Message] = asyncio.Queue()
        self._wake = asyncio.Event()
        self._turn: asyncio.Task | None = None
        self._hearing: set[str] = set()
        # Outputs paused because the person began to speak over them.
        self._hold: list[Output] | None = None
        self._last_channel = ""
        self._ids = itertools.count(1)
        self._jobs: dict[int, asyncio.Task] = {}
        self._permission: Permission | None = None
        self._answered: asyncio.Future | None = None
        self._question: Question | None = None
        self._spoken: asyncio.Future | None = None
        self._asking = asyncio.Lock()
        self._taking = False

    # -- attaching parts --------------------------------------------------

    def add_input(self, part: Input) -> None:
        self.inputs[part.name] = part
        self.state.parts["inputs"] = sorted(self.inputs)

    def add_output(self, part: Output) -> None:
        self.outputs[part.name] = part
        self.state.parts["outputs"] = sorted(self.outputs)

    def add_executor(self, part: Executor) -> None:
        self.executors[part.name] = part
        self.state.parts["executors"] = sorted(self.executors)

    # -- controls ---------------------------------------------------------

    def controls(self) -> dict[str, dict]:
        """Every switch and dial the attached parts have, by part. The
        core does not know what a microphone is; a part that has controls
        says so, and the core only passes them along."""
        found: dict[str, dict] = {}
        for part in (*self.inputs.values(), *self.outputs.values(),
                     *self.executors.values()):
            own = getattr(part, "controls", None)
            if own is not None:
                found[part.name] = dict(own)
        return found

    def control(self, part: str, name: str, value) -> dict:
        """Turn one of them. Raises KeyError for a part or a control that
        does not exist, ValueError for a value it cannot take."""
        for group in (self.inputs, self.outputs, self.executors):
            target = group.get(part)
            if target is not None and hasattr(target, "set_control"):
                if name not in target.controls:
                    raise KeyError(name)
                target.set_control(name, value)
                self.state.emit("control", part=part, name=name,
                                value=target.controls[name])
                return dict(target.controls)
        raise KeyError(part)

    async def halt(self) -> int:
        """Stop the reply and all work, now, without a word: the button's
        «стоп». Returns how many tasks were called off."""
        await self._interrupt()
        called_off = self.stop_all()
        self.state.emit("halted", tasks=called_off)
        self._refresh()
        return called_off

    # -- running ----------------------------------------------------------

    async def run(self) -> None:
        """Listen and answer until cancelled."""
        port = self.port
        tasks = [
            asyncio.create_task(part.run(port), name=f"input-{name}")
            for name, part in self.inputs.items()
        ]
        tasks.append(asyncio.create_task(self._converse(), name="core"))
        try:
            await asyncio.gather(*tasks)
        finally:
            if self._turn is not None:
                tasks.append(self._turn)
            tasks.extend(self._jobs.values())
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def hear(self, message: Message) -> None:
        """Take a message. Anything may call this: an input, the API."""
        self._hearing.discard(message.channel)
        (self._person if message.person else self._quiet).put_nowait(message)
        self._wake.set()

    def _started(self, channel: str) -> None:
        self._hearing.add(channel)
        if self._hold is None:
            pausable = [
                o for o in self.outputs.values()
                if o.speaking and "pause" in o.abilities
            ]
            for output in pausable:
                output.pause()
            self._hold = pausable or None
        self._refresh()

    def _nothing(self, channel: str) -> None:
        self._hearing.discard(channel)
        self._resume()
        self._refresh()
        self._wake.set()

    def _said_over(self, text: str, speech_ms: float, long: bool) -> bool:
        """A voice over a reply, understood. Talking past the hold is an
        interruption unless nothing was said: a cough is loud and can last
        a second, and it must not cost the sentence."""
        if self._hold is None:
            return True
        if long:
            verdict = "interrupt" if text.strip() else "resume"
        else:
            verdict = self.words.decide(text, speech_ms=speech_ms)
        if verdict == "resume":
            self._resume()
            self._refresh()
            return False
        held, self._hold = self._hold, None
        for output in held:
            output.stop()
        if verdict == "cancel" and self.words.is_halt(text, self.names):
            self.stop_all()
        return True

    def _resume(self) -> None:
        hold, self._hold = self._hold, None
        for output in hold or ():
            output.resume()

    def _refresh(self) -> None:
        if any(o.speaking for o in self.outputs.values()) and self._hold is None:
            self.state.set_phase("speaking")
        elif self._turn is not None and not self._turn.done():
            self.state.set_phase("thinking")
        elif self._hearing:
            self.state.set_phase("hearing")
        else:
            self.state.set_phase("idle")

    def _is_quiet(self) -> bool:
        return (
            (self._turn is None or self._turn.done())
            and not self._hearing
            and self._hold is None
            and not any(o.speaking for o in self.outputs.values())
        )

    @property
    def settled(self) -> bool:
        """Nothing queued, nothing being answered, nobody talking."""
        return (
            self._person.empty() and self._quiet.empty()
            and not self._taking and self._is_quiet()
        )

    async def _next(self) -> Message:
        while True:
            if not self._person.empty():
                return self._person.get_nowait()
            if not self._quiet.empty() and self._is_quiet():
                return self._quiet.get_nowait()
            self._wake.clear()
            if not self._person.empty():
                continue
            # Something is waiting for quiet: look again soon. Otherwise
            # sleep until a message arrives.
            patience = None if self._quiet.empty() else 0.05
            try:
                await asyncio.wait_for(self._wake.wait(), patience)
            except TimeoutError:
                pass

    async def _converse(self) -> None:
        while True:
            message = await self._next()
            self._taking = True
            try:
                await self._take(message)
            except Exception:
                logger.exception("turn could not be taken: %.80r", message.text)
            finally:
                self._taking = False

    async def _take(self, message: Message) -> None:
        if not message.person:
            self._turn = asyncio.create_task(self._answer(message))
            return
        held = self._hold is not None
        if not message.addressed and not held:
            # Room talk does not end a turn in progress — and while
            # something is playing, what is not said to the assistant by
            # name is the music or the film, and is left alone.
            why = ("busy" if self._turn is not None and not self._turn.done()
                   else "playing" if self.state.around.get("playing") else "")
            if why:
                self.state.emit("overheard", text=message.text,
                                channel=message.channel, why=why)
                return
        self._last_channel = message.reply_on or message.channel
        if message.addressed:
            self.state.emit("heard", text=message.text, channel=message.channel)

        if self._question is not None and self._spoken is not None:
            if self.words.is_halt(message.text, self.names):
                await self._halt(message)
                return
            self.answer(self._question.id, message.text)
            return
        if self._permission is not None:
            verdict = self.words.consent(message.text, self.names)
            if verdict is not None:
                self._resume()
                self.permit(self._permission.id, verdict)
                # A yes met with silence looks like not being heard.
                await self._interrupt()
                self._turn = asyncio.create_task(self._answer(Message(
                    text="Добре." if verdict else "Добре, не буду.",
                    channel=message.channel, reply_on=message.reply_on, verbatim=True,
                )))
                return
        if self.words.is_halt(message.text, self.names):
            await self._halt(message)
            return
        if held:
            verdict = self.words.decide(message.text, speech_ms=message.speech_ms or 0.0)
            if verdict == "resume":
                self._resume()
                self._refresh()
                return
            if verdict == "cancel":
                await self._halt(message)
                return
        await self._interrupt()
        self._turn = asyncio.create_task(self._answer(message))

    async def _interrupt(self) -> None:
        """End what is being said. The person's turn begins on a clean line."""
        self._hold = None
        for output in self.outputs.values():
            if output.speaking:
                output.stop()
        turn = self._turn
        if turn is not None and not turn.done():
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)

    async def _halt(self, message: Message) -> None:
        """«Стоп» as a turn: the reply and the work stop, and it says so.
        Never sent to the model — a command has to work every time."""
        await self._interrupt()
        called_off = self.stop_all()
        await self._keep("user", message.text, message.channel)
        self.history.append({"role": "user", "content": message.text})
        line = "Зупинив." if called_off else "Добре."
        self._turn = asyncio.create_task(self._answer(Message(
            text=line, channel=message.channel, reply_on=message.reply_on,
            verbatim=True,
        )))

    # -- one exchange -----------------------------------------------------

    def _output_for(self, message: Message) -> Output | None:
        """Where the question came from; for news nobody asked for, where
        the person last was."""
        name = (
            message.reply_on or message.channel if message.person
            else self._last_channel
        )
        return self.outputs.get(name) or next(iter(self.outputs.values()), None)

    async def _answer(self, message: Message) -> None:
        out = self._output_for(message)
        try:
            if message.verbatim:
                # Part of the conversation the model reads next turn:
                # without «Зупинив.» in it, it went on describing the
                # work it had just been told to stop.
                self.history.append({"role": "assistant", "content": message.text})
                await self._say(out, message.text)
                await self._keep("assistant", message.text, out.name if out else "")
                self.state.emit("said", text=message.text,
                                channel=out.name if out else "", failed=None)
                return
            await self._exchange(message, out)
        finally:
            self._turn = None if self._turn is asyncio.current_task() else self._turn
            self._refresh()
            self._wake.set()

    async def _exchange(self, message: Message, out: Output | None) -> None:
        # Room talk is answered in one go: the model either replies or
        # says the one word for silence, and that word never reaches an
        # output. (It used to be asked twice — once to decide, once to
        # answer — and every such reply cost a second of waiting.)
        judged = not message.addressed
        system = await self._system()
        if judged:
            system = f"{system}\n\n{self.judge}"
        elif message.person:
            await self._keep("user", message.text, message.channel)
        self.history.append({"role": "user", "content": message.text})
        messages = [
            {"role": "system", "content": system},
            *self.history[-HISTORY_TURNS:],
        ]
        self.state.set_phase("thinking")

        failure: str | None = None
        reply, calls, outcome = "", [], "done"
        try:
            reply, calls, outcome, broke = await self._voice(out, messages, judged)
            if judged:
                if not reply and not calls:
                    # It chose silence. Nothing is kept: what was not
                    # said to it is not its to remember.
                    self.history.pop()
                    self.state.emit("overheard", text=message.text,
                                    channel=message.channel, why="chose silence")
                    return
                self.state.emit("heard", text=message.text, channel=message.channel)
                await self._keep("user", message.text, message.channel)
            if broke:
                failure, reply = "model", ""
            elif outcome != "stopped":
                for call in calls:
                    spoken = await self._run_verb(call)
                    # The model already said something; «Беру.» on top of
                    # «Зараз гляну.» is the same thing said twice. A verb
                    # that only acknowledges speaks when the model did not.
                    if reply and call.get("name") in self._acknowledging():
                        continue
                    if spoken:
                        reply = f"{reply} {spoken}".strip()
                        outcome = await self._say(out, spoken)
                        if outcome == "stopped":
                            break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("output failed: reply written but not delivered")
            failure = "output"

        if reply:
            self.history.append({"role": "assistant", "content": reply})
        if failure is not None:
            await self._say_failure(out)
        recorded = reply or (FAILURE_LINE if failure else "")
        if recorded:
            await self._keep("assistant", recorded, out.name if out else "")
        self.state.emit("said", text=recorded, channel=out.name if out else "",
                        failed=failure)

        if (
            failure is None and not calls and message.person
            and outcome != "stopped" and self._verbs()
        ):
            if self.words.promises_work(reply) or (
                len(self._acknowledging()) > 2 and self.words.claims_done(reply)
            ):
                await self._keep_the_promise(messages, reply, out)

    async def _voice(
        self, out: Output | None, messages: list[dict], may_be_silent: bool = False
    ) -> tuple[str, list[dict], str, bool]:
        """The model's reply, delivered while it is written. Returns the
        text, the verbs it called, how delivery ended, and whether the
        model fell over — which is not the output's failure and must not
        be reported as one. With ``may_be_silent`` the first few letters
        are held back until it is clear they are not the word for
        silence; if they are, nothing is delivered and the text is empty."""
        parts: list[str] = []
        calls: list[dict] = []
        broke = False
        held, decided = "", not may_be_silent

        async def pieces() -> AsyncIterator[str]:
            nonlocal broke, held, decided
            try:
                async for event in self.model.reply(messages, self._schemas()):
                    kind = event.get("type")
                    if kind == "delta":
                        text = event["text"]
                        if not decided:
                            held += text
                            start = held.strip().upper()
                            if SILENCE.startswith(start) or start.startswith(SILENCE):
                                continue  # silence, or still could be
                            decided, text = True, held
                        if not parts:
                            self.state.set_phase("speaking")
                        parts.append(text)
                        yield text
                    elif kind == "tool_call":
                        calls.append(event)
                    elif kind == "usage":
                        self.state.emit(
                            "usage", **{k: v for k, v in event.items() if k != "type"}
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("model failed mid-reply")
                broke = True
            if not decided and held.strip() and not held.strip().upper().startswith(SILENCE):
                parts.append(held)  # a short answer that only looked like the start
                yield held

        if out is None:
            async for _ in pieces():
                pass
            outcome = "done"
        else:
            outcome = await out.say(pieces())
        return "".join(parts).strip(), calls, outcome, broke

    async def _keep_the_promise(
        self, messages: list[dict], reply: str, out: Output | None
    ) -> None:
        """«Гляну, секунду» with no verb called: ask once more for the
        call. That is all — handing the person's words to an executor
        when the call still did not come turned a refusal («скажи, і я
        зроблю») into a job nobody asked for."""
        again = [
            *messages,
            {"role": "assistant", "content": reply},
            {"role": "user", "content": KEEP_THE_PROMISE},
        ]
        calls: list[dict] = []
        try:
            async for event in self.model.reply(again, self._schemas()):
                if event.get("type") == "tool_call":
                    calls.append(event)
        except Exception:
            logger.exception("asking for the promised call failed")
            return
        logger.info("promise without a call: asked again, got %d call(s)", len(calls))
        for call in calls:
            spoken = await self._run_verb(call)
            # The model already said it; an acknowledgement again is noise.
            if spoken and call.get("name") not in self._acknowledging():
                await self._say(out, spoken)

    async def _system(self) -> str:
        prompt = DEFAULT_PROMPT
        if self._prompt is not None:
            try:
                prompt = await self._prompt()
            except Exception:
                logger.exception("prompt could not be built — using the default")
        if self.memory is not None:
            try:
                known = await self.memory.always()
            except Exception:
                logger.exception("memory.always failed (non-fatal)")
                known = ""
            if known:
                prompt = f"{prompt}\n\n{known}"
        for output in self.outputs.values():
            shown = getattr(output, "context", lambda: "")()
            if shown:
                prompt = f"{prompt}\n\n{shown}"
        return f"{prompt}{self._known()}"

    def _known(self) -> str:
        """What it knows without being told: the work and the room. Asked
        «що ти зараз робиш?» after everything was stopped, it answered
        that the work was under way — it had no way to know."""
        # Stated as the system's record, not as the assistant's own past:
        # worded «справи, які ти доручав», the model began to answer
        # «доручив агенту» to every new request and call nothing.
        lines = []
        for task in list(self.state.tasks.values())[-5:]:
            status = {"running": "У РОБОТІ", "done": "готово", "failed": "не вийшло",
                      "stopped": "зупинено"}.get(task.status, task.status)
            lines.append(f"- {status}: {task.request[:90]}")
        busy = sum(t.status == "running" for t in self.state.tasks.values())
        known = ("\n\nЗапис системи про справи (не твої слова). Зараз у роботі: "
                 f"{busy}" + ("" if busy else " — тобто зараз ти нічого не робиш")
                 + "." + ("\n" + "\n".join(lines) if lines else "")
                 + "\nСправа існує лише тоді, коли вона є в цьому записі. Нове прохання "
                 "про роботу — це завжди новий виклик delegate; сказати «доручив» без "
                 "виклику означає нічого не зробити.") if self.executors else ""
        playing = self.state.around.get("playing")
        if playing:
            known += ("\n\nЗараз поруч грає звук (" + ", ".join(map(str, playing)) + "). "
                      "Поки він грає, ти відповідаєш лише на пряме звертання на ім'я.")
        return known

    async def _say(self, out: Output | None, text: str) -> str:
        if out is None or not text:
            return "done"
        return await out.say(_one(text))

    async def _say_failure(self, out: Output | None) -> None:
        try:
            await self._say(out, FAILURE_LINE)
        except Exception:
            logger.exception("the failure line could not be delivered either")
            self.state.emit("failed", channel=out.name if out else "")

    async def _keep(self, role: str, text: str, channel: str) -> None:
        """Memory must never break the conversation."""
        if self.memory is None:
            return
        try:
            await self.memory.keep(role, text, channel)
        except Exception:
            logger.exception("memory.keep failed (non-fatal)")

    # -- verbs ------------------------------------------------------------

    def _verbs(self) -> dict[str, tuple[dict, Callable[[dict], Awaitable[str]]]]:
        """What the model may call. A verb that cannot do anything here is
        not offered: a tool the model can see is a promise."""
        verbs: dict[str, tuple[dict, Callable[[dict], Awaitable[str]]]] = {}
        if self.memory is not None:
            verbs["recall"] = (_RECALL, self._recall)
        if self.executors:
            verbs["delegate"] = (_DELEGATE, self._delegate)
        if self._jobs:
            verbs["stop_work"] = (_STOP, self._stop_work)
        # What an output lets the model write on directly (a canvas).
        for output in self.outputs.values():
            for schema, run in getattr(output, "verbs", lambda: ())():
                verbs[schema["function"]["name"]] = (schema, run)
        return verbs

    def _acknowledging(self) -> set[str]:
        """Verbs whose answer is only «done»: said when nothing else was."""
        names = {"delegate", "stop_work"}
        for output in self.outputs.values():
            for schema, _ in getattr(output, "verbs", lambda: ())():
                names.add(schema["function"]["name"])
        return names

    def _schemas(self) -> list[dict]:
        return [schema for schema, _ in self._verbs().values()]

    async def _run_verb(self, call: dict) -> str:
        """A verb's answer is said as returned, so each returns a finished
        sentence and none raises into the turn."""
        name = call.get("name", "")
        arguments = call.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except ValueError:
                arguments = {}
        found = self._verbs().get(name)
        if found is None:
            self.state.emit("cannot", what=name, why="no such verb")
            return "Такої дії я не знаю."
        self.state.emit("verb", name=name, arguments=arguments)
        try:
            return await found[1](arguments)
        except Exception:
            logger.exception("verb %s failed: %.200s", name, arguments)
            return "Не вийшло. Спробуй ще раз."

    async def _recall(self, arguments: dict) -> str:
        query = str(arguments.get("query", "")).strip()
        if not query:
            return "Що саме згадати?"
        return await self.memory.recall(query, str(arguments.get("when", "") or ""))

    async def _delegate(self, arguments: dict) -> str:
        request = str(arguments.get("task", "")).strip()
        if not request:
            return "Скажи, що саме зробити."
        if self._same(request) is not None:
            return "Це вже в роботі."
        self.delegate(request)
        return "Беру."

    async def _stop_work(self, arguments: dict) -> str:
        return "Зупинив." if self.stop_all() else "Нічого не було запущено."

    def _same(self, request: str) -> Task | None:
        """A piece of work already running that this request repeats. One
        request is one job: asked again before it is done, it is the same
        job, not a second one."""
        asked = set(self.words.tokens(request))
        for task in self.state.tasks.values():
            if task.status != "running":
                continue
            running = set(self.words.tokens(task.request))
            common = len(asked & running)
            if common and common >= 0.5 * min(len(asked), len(running)):
                return task
        return None

    # -- work -------------------------------------------------------------

    def delegate(self, request: str) -> Task:
        """Hand a piece of work off. Returns at once; the result arrives
        later as a message of its own, in the quiet lane."""
        task = Task(next(self._ids), request, executor="")
        self.state.tasks[task.id] = task
        job = asyncio.create_task(self._work(task), name=f"task-{task.id}")
        self._jobs[task.id] = job
        job.add_done_callback(lambda _: self._jobs.pop(task.id, None))
        return task

    async def _work(self, task: Task) -> None:
        try:
            name, text = await self.delegator.assign(
                task.request, self.history[-CONTEXT_TURNS:],
                list(self.executors.values()),
            )
            task.executor = name
            self.state.emit("task", **task.view())
            task.result = await self.executors[name].run(text, _Desk(self, task))
            task.status = "done"
        except asyncio.CancelledError:
            task.status = "stopped"
            task.ended = time.time()
            self.state.emit("task", **task.view())
            raise
        except Exception as exc:  # failed work must still be reported
            logger.exception("task %d failed: %.120s", task.id, task.request)
            task.status, task.result = "failed", f"Не вдалося: {exc}"
        try:
            news = await self.delegator.brief(task)
        except Exception:
            logger.exception("briefing failed — saying the result as it came")
            news = task.result
        # Named only when others are still running: one result needs no
        # label, two arriving out of order cannot be told apart without.
        others = any(t.status == "running" for t in self.state.tasks.values())
        tag = f" [{task.request[:40]}]" if others else ""
        task.ended = time.time()
        # The engine delivers it: the core says only that a piece of work
        # ended and what came of it, and never puts it into the conversation
        # itself. Delivery is the engine's job now — see docs/engine.md, D3.
        self.state.emit("task", **task.view(), news=f"{tag} {news}".strip())

    def stop_task(self, task_id: int) -> bool:
        job = self._jobs.get(task_id)
        if job is None or job.done():
            return False
        job.cancel()
        return True

    def stop_all(self) -> int:
        """Call off everything in flight. «Стоп» has to mean stop."""
        return sum(self.stop_task(task_id) for task_id in list(self._jobs))

    # -- permission -------------------------------------------------------

    async def _ask(self, task: Task, what: str, kind: str) -> bool:
        """Reading is free; a change waits for the person's yes. The yes
        is caught by code from the person's own lane, never read out of a
        model's reply or an executor's result."""
        if kind == "read":
            return True
        async with self._asking:
            # Asked once for the whole piece of work. Thirty questions in
            # ten minutes, one per command, made talking impossible.
            if task.allowed is not None:
                return task.allowed
            permission = Permission(next(self._ids), task.id, task.executor, what)
            self._answered = asyncio.get_running_loop().create_future()
            self._permission = self.state.permission = permission
            self.state.emit("permission", **permission.view())
            # Said short: a shell command read out in full is noise. The
            # whole of it is in the event, for a screen to show.
            short = what if len(what) <= SAY_CHARS else what[:SAY_CHARS].rstrip() + "…"
            self.hear(Message(
                text=(f"{task.executor} просить дозволу на зміни для цієї справи. "
                      f"Почне з: {short}. Дозволяєш?"),
                channel=task.executor, person=False, verbatim=True,
            ))
            allowed = False
            try:
                allowed = await asyncio.wait_for(
                    self._answered, self.permission_timeout
                )
            except TimeoutError:
                logger.info("permission %d: no answer — refused", permission.id)
            finally:
                task.allowed = allowed
                self._permission = self.state.permission = None
                self.state.emit("permission_closed", id=permission.id, allowed=allowed)
            return allowed

    def permit(self, permission_id: int, allowed: bool) -> bool:
        """Answer the open question — by voice, by text, or by a button."""
        permission, answered = self._permission, self._answered
        if permission is None or permission.id != permission_id:
            return False
        if answered is not None and not answered.done():
            answered.set_result(bool(allowed))
        return True

    async def _question_of(self, task: Task, text: str) -> str:
        """An executor asking the person mid-work, in words.

        The answer is the person's own next message, caught from their lane
        — never a model's guess at what they would have said.
        """
        async with self._asking:
            asked = Question(next(self._ids), task.id, task.executor, text)
            self._spoken = asyncio.get_running_loop().create_future()
            self._question = self.state.question = asked
            self.state.emit("question", **asked.view())
            short = text if len(text) <= SAY_CHARS else text[:SAY_CHARS].rstrip() + "…"
            self.hear(Message(
                text=f"{task.executor} питає: {short}",
                channel=task.executor, person=False, verbatim=True,
            ))
            answer = ""
            try:
                answer = await asyncio.wait_for(self._spoken, QUESTION_TIMEOUT_S)
            except TimeoutError:
                logger.info("question %d: no answer", asked.id)
            finally:
                self._question = self.state.question = None
                self.state.emit("question_closed", id=asked.id, answer=answer)
            return answer

    def answer(self, question_id: int, text: str) -> bool:
        """The person's words for the open question."""
        asked, spoken = self._question, self._spoken
        if asked is None or asked.id != question_id:
            return False
        if spoken is not None and not spoken.done():
            spoken.set_result(str(text))
        return True


_RECALL = {
    "type": "function",
    "function": {
        "name": "recall",
        "description": (
            "Search what was actually said in your past conversations with "
            "this person — whenever they ask what they told you, what you "
            "agreed, or when something came up."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The words to look for — the subject, "
                                   "not the whole question.",
                },
                "when": {
                    "type": "string",
                    "description": "Their own words about when, if they said "
                                   "any: «вчора», «минулого тижня». Leave out "
                                   "if they did not.",
                },
            },
            "required": ["query"],
        },
    },
}

_STOP = {
    "type": "function",
    "function": {
        "name": "stop_work",
        "description": (
            "Stop all the work that is running now. Whenever the person asks "
            "to stop, cancel or call off what the agents are doing, in any "
            "words. Never say it is stopped without calling this."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

_DELEGATE = {
    "type": "function",
    "function": {
        "name": "delegate",
        "description": (
            "Hand work to an agent. Everything outside this conversation "
            "goes through here: files, code, the shell, the web, other "
            "programs. You cannot see or touch any of it yourself, so never "
            "say you cannot before you have asked. Returns at once: say in "
            "one short sentence that you are on it, then stop. The answer "
            "arrives later as its own message."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "What the person asked for, as they said it.",
                },
            },
            "required": ["task"],
        },
    },
}
