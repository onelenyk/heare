"""The five kinds of part, and exactly what the core asks of each.

Everything that touches the world is one of these: an input, an output,
a model, a memory, an executor. The core knows the forms and nothing
behind them — not that one input is a microphone, not which company's
model is answering, not which agent is doing the work. A new thing is
connected properly when it has the form and passes the form's test
(``tests/heare/forms.py``).

A channel (voice, text, a screen) is not a sixth kind: it is one object
that is an input, an output, or both.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol


@dataclass(frozen=True)
class Message:
    """One finished thing said to the assistant.

    ``person`` decides the lane. The person's own words — spoken, typed,
    clicked — interrupt whatever is happening. Everything else — an
    executor's result, a reminder — waits until nobody is talking.
    """

    text: str
    channel: str
    person: bool = True
    # Where to answer; empty means the channel it arrived on.
    reply_on: str = ""
    # How long the voice lasted, when it was a voice. A nod is short.
    speech_ms: float | None = None
    # False when it was said in the room and nothing marks it as meant
    # for the assistant. The assistant then decides for itself whether
    # there is anything worth saying, and may say nothing.
    addressed: bool = True
    # Said exactly as written, without asking the model: a question the
    # code needs answered, not one the model should rephrase.
    verbatim: bool = False
    at: float = field(default_factory=time.time)


class Port(Protocol):
    """What the core hands an input: three things it may report."""

    def started(self, channel: str) -> None:
        """The person began. Nothing is known yet about what they will say."""

    def heard(self, message: Message) -> None:
        """The person finished a turn, or something else has a message."""

    def nothing(self, channel: str) -> None:
        """What began was not a turn after all: a cough, a breath."""

    def overheard(self, channel: str, text: str) -> None:
        """Something was said and understood, but not to the assistant.
        It is not answered — and it is shown, because silence with no
        reason looks exactly like being deaf."""

    def said_over(self, channel: str, text: str, speech_ms: float, long: bool) -> bool:
        """What was said over a reply, as soon as it is known and before
        the turn is complete. The core answers whether it is a turn: a nod
        is not, and the reply carries on; anything else ends the reply now
        rather than a turn's wait later."""


class Input(Protocol):
    name: str

    async def run(self, port: Port) -> None:
        """Deliver messages until cancelled. When a turn is over is the
        input's own business: silence for a voice, Enter for text."""


class Output(Protocol):
    name: str
    # What it can do beyond saying and stopping. "pause": hold a reply
    # and carry on with it, which is what makes a nod not an interruption.
    abilities: frozenset[str]

    @property
    def speaking(self) -> bool: ...

    async def say(self, pieces: AsyncIterator[str]) -> str:
        """Deliver a reply as it is written. Returns ``"done"``, or
        ``"stopped"`` when it was cut. Raises when nothing could be
        delivered at all — the core then says so instead."""

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def stop(self) -> None: ...


class Model(Protocol):
    def reply(self, messages: list[dict], verbs: list[dict]) -> AsyncIterator[dict]:
        """Events as they come: ``{"type": "delta", "text": …}`` and
        ``{"type": "tool_call", "name": …, "arguments": {…}}``."""


class Memory(Protocol):
    async def keep(self, role: str, text: str, channel: str) -> None:
        """Write one side of one turn down. Always, not when a model thinks to."""

    async def recall(self, query: str, when: str = "") -> str:
        """What was said before, as one finished sentence to say aloud."""

    async def always(self) -> str:
        """What belongs in every prompt: who the person is, what was agreed."""


class Desk(Protocol):
    """What the core hands an executor for the length of one task."""

    def step(self, text: str) -> None:
        """What it is doing now, in a few words. This is what makes work visible."""

    async def permit(self, what: str, kind: str = "change") -> bool:
        """Ask before acting. ``kind`` is ``"read"`` or ``"change"``."""


class Executor(Protocol):
    name: str
    # One line a model can choose by: what this one is good for.
    about: str

    async def run(self, task: str, desk: Desk) -> str:
        """Do the task and return what came of it. Being cancelled is how
        it is told to stop, and it must stop."""


class Delegator(Protocol):
    """Turns a request into a task for somebody, and a result into news."""

    async def assign(
        self, request: str, history: list[dict], executors: list[Executor]
    ) -> tuple[str, str]:
        """Who should do it, and the task in full as they should read it."""

    async def brief(self, task: Task) -> str:
        """The result, cut down to what is worth saying aloud."""


@dataclass
class Task:
    """One piece of delegated work, visible from start to end."""

    id: int
    request: str
    executor: str
    status: str = "running"  # running · done · failed · stopped
    step: str = ""
    result: str = ""
    started: float = field(default_factory=time.time)
    ended: float | None = None

    def view(self) -> dict:
        return {
            "id": self.id, "request": self.request, "executor": self.executor,
            "status": self.status, "step": self.step, "result": self.result,
            "started": self.started, "ended": self.ended,
        }


@dataclass
class Permission:
    """A question waiting for the person's yes or no."""

    id: int
    task: int
    executor: str
    what: str

    def view(self) -> dict:
        return {"id": self.id, "task": self.task, "executor": self.executor,
                "what": self.what}
