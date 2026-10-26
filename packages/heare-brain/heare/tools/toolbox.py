"""The catalog of abilities the model may call, assembled outside the core.

The core knows the shape of a tool (``heare.core.forms.Tools``) and runs
whichever one is offered; what the tools are, and when each may be
offered, lives here.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from heare.core.forms import Memory, Task
from heare.core.state import State
from heare.tools import delegate as delegate_tool
from heare.tools import devices as device_tools
from heare.tools import recall, reminders as reminder_tools
from heare.tools import schemas
from heare.tools.ports import Devices, Reminders
from heare.tools.schemas import (
    _CANCEL_REMINDER,
    _DELEGATE,
    _LIST_REMINDERS,
    _RECALL,
    _REMIND,
    _STOP,
)


class Toolbox:
    def __init__(
        self,
        *,
        memory: Memory | None,
        state: State,
        delegate: Callable[[str, bool], Task],
        stop: Callable[[], int],
        words: Any,
        has_executors: Callable[[], bool],
        reminders: Reminders | None = None,
        now: Callable[[], float] = time.time,
        devices: Callable[[], Devices | None] = lambda: None,
        ask: Callable[[str, str], Awaitable[bool]] | None = None,
        say: Callable[[str, str], None] | None = None,
    ) -> None:
        self._memory = memory
        self._state = state
        self._start = delegate
        self._stop = stop
        self._words = words
        self._has_executors = has_executors
        self._reminders = reminders
        self._now = now
        self._devices = devices
        self._ask = ask
        self._say = say

    def verbs(self) -> dict[str, tuple[dict, Callable[[dict], Awaitable[str]]]]:
        verbs: dict[str, tuple[dict, Callable[[dict], Awaitable[str]]]] = {}
        if self._memory is not None:
            verbs["recall"] = (_RECALL, self._run_recall)
        if self._has_executors():
            verbs["delegate"] = (_DELEGATE, self._run_delegate)
        if any(task.status == "running" for task in self._state.tasks.values()):
            verbs["stop_work"] = (_STOP, self._run_stop)
        if self._reminders is not None:
            verbs["remind"] = (_REMIND, self._run_remind)
            verbs["list_reminders"] = (_LIST_REMINDERS, self._run_list_reminders)
            verbs["cancel_reminder"] = (_CANCEL_REMINDER, self._run_cancel_reminder)
        here = device_tools.found(self._devices())
        if here is not None and self._ask is not None and self._say is not None:
            pins = device_tools.pins(here[1])
            verbs["panel"] = (schemas.panel(pins), self._run_panel)
            if pins:
                verbs["read_pin"] = (schemas.read_pin(pins), self._run_read_pin)
        return verbs

    def acknowledging(self) -> set[str]:
        return {"delegate", "stop_work", "remind", "cancel_reminder", "panel"}

    def read_only(self, name: str) -> bool:
        return name in {"recall", "list_reminders", "read_pin"}

    async def _run_recall(self, arguments: dict) -> str:
        return await recall.run(self._memory, arguments)

    async def _run_delegate(self, arguments: dict) -> str:
        return await delegate_tool.run(
            self._start, self._state, self._words, arguments
        )

    async def _run_stop(self, arguments: dict) -> str:
        return "Зупинив." if self._stop() else "Нічого не було запущено."

    async def _run_remind(self, arguments: dict) -> str:
        return await reminder_tools.remind(
            self._reminders, self._now(), arguments
        )

    async def _run_list_reminders(self, arguments: dict) -> str:
        return await reminder_tools.listed(self._reminders, self._now())

    async def _run_cancel_reminder(self, arguments: dict) -> str:
        return await reminder_tools.cancel(self._reminders, arguments)

    async def _run_panel(self, arguments: dict) -> str:
        devices = self._devices()
        if devices is None or self._ask is None or self._say is None:
            return "Панелі зараз немає."
        return await device_tools.run(devices, self._ask, self._say, arguments)

    async def _run_read_pin(self, arguments: dict) -> str:
        return device_tools.read(self._devices(), arguments)
