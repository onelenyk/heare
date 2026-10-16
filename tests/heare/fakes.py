"""Stand-ins for every kind of part, and a room to drive the core in.

Each fake has the form and nothing else, so a scenario here is the core
talking to the forms — which is all the core is ever allowed to know.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from heare.core import Core, Message
from heare.language import uk


class Line:
    """An input the test speaks through."""

    def __init__(self, name: str = "text") -> None:
        self.name = name
        self.port: Any = None

    async def run(self, port) -> None:
        self.port = port
        await asyncio.Event().wait()


class Page:
    """An output that keeps what it was given.

    ``slow`` makes it take that long per piece, so there is a reply to
    talk over. A paused page holds its place; a stopped one ends.
    """

    def __init__(self, name: str = "text", *, pause: bool = True,
                 slow: float = 0.0, broken: bool = False) -> None:
        self.name = name
        self.abilities = frozenset({"pause"}) if pause else frozenset()
        self.slow, self.broken = slow, broken
        self.said: list[str] = []
        self.outcomes: list[str] = []
        self.paused = False
        self.pauses = 0
        self._stop = False
        self._busy = False

    @property
    def speaking(self) -> bool:
        return self._busy

    async def say(self, pieces: AsyncIterator[str]) -> str:
        if self.broken:
            raise RuntimeError("the output is broken")
        self._busy, self._stop = True, False
        parts: list[str] = []
        outcome = "done"
        try:
            async for piece in pieces:
                parts.append(piece)
                waited = 0.0
                while waited < self.slow or self.paused:
                    if self._stop:
                        break
                    await asyncio.sleep(0.01)
                    waited += 0.0 if self.paused else 0.01
                if self._stop:
                    outcome = "stopped"
                    break
        except asyncio.CancelledError:
            outcome = "stopped"
            raise
        finally:
            self._busy = False
            if parts:
                self.said.append("".join(parts))
            self.outcomes.append(outcome)
        return outcome

    def pause(self) -> None:
        self.paused = True
        self.pauses += 1

    def resume(self) -> None:
        self.paused = False

    def stop(self) -> None:
        self._stop, self.paused = True, False


@dataclass
class Says:
    text: str = ""
    calls: tuple[tuple[str, dict], ...] = ()
    fails: bool = False


class Script:
    """A model that answers from a list, and remembers what it was asked."""

    def __init__(self) -> None:
        self.answers: list[Says] = []
        self.asked: list[list[dict]] = []
        self.offered: list[list[str]] = []

    def will_say(self, *answers: Says | str) -> None:
        self.answers.extend(a if isinstance(a, Says) else Says(a) for a in answers)

    async def reply(self, messages: list[dict], verbs: list[dict]):
        self.asked.append(messages)
        self.offered.append([v["function"]["name"] for v in verbs])
        answer = self.answers.pop(0) if self.answers else Says("Не знаю, що сказати.")
        for word in answer.text.split(" ") if answer.text else []:
            yield {"type": "delta", "text": word + " "}
        if answer.fails:
            raise RuntimeError("the model fell over")
        for name, arguments in answer.calls:
            yield {"type": "tool_call", "name": name, "arguments": arguments}


class Notebook:
    def __init__(self, found: str = "Ти казав про це вчора.") -> None:
        self.kept: list[tuple[str, str, str]] = []
        self.found = found
        self.known = ""

    async def keep(self, role: str, text: str, channel: str) -> None:
        self.kept.append((role, text, channel))

    async def recall(self, query: str, when: str = "") -> str:
        return self.found

    async def always(self) -> str:
        return self.known


class Worker:
    """An executor that finishes when the test lets it."""

    def __init__(self, name: str = "worker", result: str = "Готово.",
                 asks: tuple[tuple[str, str], ...] = ()) -> None:
        self.name, self.about = name, "робить усе"
        self.result, self.asks = result, asks
        self.tasks: list[str] = []
        self.allowed: list[bool] = []
        self.release = asyncio.Event()
        self.stopped = 0

    async def run(self, task: str, desk) -> str:
        self.tasks.append(task)
        try:
            for what, kind in self.asks:
                self.allowed.append(await desk.permit(what, kind))
            desk.step("працюю")
            await self.release.wait()
        except asyncio.CancelledError:
            self.stopped += 1
            raise
        return self.result


@dataclass
class Room:
    core: Core
    model: Script
    line: Line
    page: Page
    memory: Notebook
    _run: Any = None
    _motor: Any = None
    extra: dict = field(default_factory=dict)

    def says(self, text: str, *, channel: str = "", ms: float | None = None) -> None:
        self.core.hear(Message(text=text, channel=channel or self.line.name,
                               speech_ms=ms))

    def starts(self, channel: str = "") -> None:
        self.line.port.started(channel or self.line.name)

    async def settled(self, timeout: float = 3.0) -> None:
        """Wait until nothing is queued, nothing is being answered."""
        core = self.core
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.02)
            if core.settled:
                return
        raise AssertionError("the core never settled")

    async def until(self, check, timeout: float = 3.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if check():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("timed out waiting")

    async def close(self) -> None:
        tasks = [self._run] + ([self._motor] if self._motor is not None else [])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def open_room(*, memory: bool = True, slow: float = 0.0, **core_kw) -> Room:
    import tempfile
    from pathlib import Path

    from heare.engine import Engine, Intents

    model, line, page = Script(), Line(), Page(slow=slow)
    notebook = Notebook()
    core = Core(model, memory=notebook if memory else None,
                names=frozenset({"дока"}), words=uk, **core_kw)
    core.add_input(line)
    core.add_output(page)
    room = Room(core, model, line, page, notebook)
    # Delivery is the engine's now: a scenario that expects a result to come
    # back needs the engine running, exactly as the app runs it.
    motor = Engine(Intents(Path(tempfile.mkdtemp()) / "intents.json"),
                   state=core.state, speak=core.hear, gap=0.0)
    room._run = asyncio.create_task(core.run())
    room._motor = asyncio.create_task(motor.run())
    await asyncio.sleep(0.01)
    return room
