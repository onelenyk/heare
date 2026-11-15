"""The catalog of abilities the model may call, assembled outside the core.

The core knows the shape of a tool (``heare.core.forms.Tools``) and runs
whichever one is offered; what the tools are, and when each may be
offered, lives here.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from heare.core.forms import Memory, Task
from heare.core.i18n import _
from heare.core.state import State
from heare.tools import delegate as delegate_tool
from heare.tools import bodies as body_tools
from heare.tools import recall, reminders as reminder_tools
from heare.tools import schemas
from heare.tools import plan as plan_tool
from heare.tools import sessions as sessions_tool
from heare.tools import undo as undo_tool
from heare.tools.ports import Bodies, Plans, Reminders
from heare.tools.schemas import (
    _CANCEL_REMINDER,
    _LIST_REMINDERS,
    _MERGE_PLANS,
    _PIN,
    _PLANS,
    _READ_BODY,
    _RECALL,
    _REMIND,
    _CONTINUE,
    _PLAN,
    _SESSIONS,
    _STOP,
    _UNDO,
)


class Toolbox:
    def __init__(
        self,
        *,
        memory: Memory | None,
        state: State,
        delegate: Callable[[str, bool, str], Task],
        stop: Callable[[], int],
        undo: Callable[[str], Task] | None = None,
        plan: Callable[[str, str], Task] | None = None,
        recent: Callable[[str], Task] | None = None,
        continue_work: Callable[[str, str, str, bool], Task] | None = None,
        words: Any | Callable[[], Any],
        has_executors: Callable[[], bool],
        reminders: Reminders | None = None,
        now: Callable[[], float] = time.time,
        hands: Callable[[], list[tuple[str, str, str, str]]] = lambda: [],
        bodies: Callable[[], Bodies | None] = lambda: None,
        plans: Plans | None = None,
        pins: object | None = None,
    ) -> None:
        self._memory = memory
        self._state = state
        self._start = delegate
        self._stop = stop
        self._undo = undo
        self._plan = plan
        self._recent = recent
        self._continue = continue_work
        self._words = words
        self._has_executors = has_executors
        self._reminders = reminders
        self._now = now
        self._hands = hands
        self._bodies = bodies
        self._plans = plans
        self._pins = pins

    def verbs(self) -> dict[str, tuple[dict, Callable[[dict], Awaitable[str]]]]:
        verbs: dict[str, tuple[dict, Callable[[dict], Awaitable[str]]]] = {}
        if self._memory is not None:
            verbs["recall"] = (_RECALL, self._run_recall)
        if self._has_executors():
            verbs["delegate"] = (schemas.delegate(self._hands()), self._run_delegate)
            if self._undo is not None:
                verbs["undo"] = (_UNDO, self._run_undo)
            if self._plan is not None:
                verbs["plan"] = (_PLAN, self._run_plan)
            if self._recent is not None:
                verbs["recent_work"] = (_SESSIONS, self._run_recent)
            if self._continue is not None:
                verbs["continue_work"] = (_CONTINUE, self._run_continue)
        if any(task.status == "running" for task in self._state.tasks.values()):
            verbs["stop_work"] = (_STOP, self._run_stop)
        if self._plans is not None:
            verbs["plans"] = (_PLANS, self._run_plans)
            verbs["merge_plans"] = (_MERGE_PLANS, self._run_merge_plans)
        if self._reminders is not None:
            verbs["remind"] = (_REMIND, self._run_remind)
            verbs["list_reminders"] = (_LIST_REMINDERS, self._run_list_reminders)
            verbs["cancel_reminder"] = (_CANCEL_REMINDER, self._run_cancel_reminder)
        bodies = self._bodies()
        if bodies is not None and any(
            s["effect"] == "read"
            for entry in bodies.skilled().values() for s in entry["skills"]
        ):
            verbs["read_body"] = (_READ_BODY, self._run_read_body)
        if self._pins is not None:
            verbs["pin"] = (_PIN, self._run_pin)
        return verbs

    def acknowledging(self) -> set[str]:
        return {"delegate", "undo", "plan", "recent_work", "continue_work",
                "stop_work", "remind", "cancel_reminder", "merge_plans"}

    def read_only(self, name: str) -> bool:
        return name in {"recall", "list_reminders", "read_body", "plans"}

    def lookup(self, name: str) -> bool:
        # remind and cancel_reminder change something, but only locally and
        # without permission; their answer is worth seeing inside the turn.
        return name in {"recall", "list_reminders", "read_body", "pin",
                        "remind", "cancel_reminder", "plans", "merge_plans"}

    async def _run_pin(self, arguments: dict) -> str:
        from heare.tools import pins
        return pins.run(self._pins, arguments)

    async def _run_recall(self, arguments: dict) -> str:
        return await recall.run(self._memory, arguments)

    async def _run_delegate(self, arguments: dict) -> str:
        words = self._words() if callable(self._words) else self._words
        return await delegate_tool.run(
            self._start, self._state, words, arguments
        )

    async def _run_undo(self, arguments: dict) -> str:
        assert self._undo is not None
        return await undo_tool.run(self._undo, self._hands(), arguments)

    async def _run_plan(self, arguments: dict) -> str:
        assert self._plan is not None
        return await plan_tool.run(self._plan, self._hands(), arguments)

    async def _run_recent(self, arguments: dict) -> str:
        assert self._recent is not None
        return await sessions_tool.recent(self._recent, self._hands(), arguments)

    async def _run_continue(self, arguments: dict) -> str:
        assert self._continue is not None
        return await sessions_tool.continuing(
            self._continue, self._hands(), arguments)

    async def _run_read_body(self, arguments: dict) -> str:
        bodies = self._bodies()
        if bodies is None:
            return "No bodies are connected now."
        return await body_tools.read(bodies, arguments)

    async def _run_plans(self, arguments: dict) -> str:
        plans = self._plans.plans() if self._plans is not None else []
        if not plans:
            return "Nothing is on your mind."
        return "\n".join(
            f"{plan['id']} · {plan['label']} (pressing {plan['pressing']:.2f}, "
            f"said {plan['said']})"
            for plan in plans
        )

    async def _run_merge_plans(self, arguments: dict) -> str:
        if self._plans is None:
            return _("No plans are open.")
        keep = str(arguments.get("keep") or "")
        drop = str(arguments.get("drop") or "")
        label = str(arguments.get("label") or "")
        return _("Merged.") if self._plans.merge_plans(keep, drop, label) else _("No such plan.")

    async def _run_stop(self, arguments: dict) -> str:
        return _("Stopped.") if self._stop() else _("Nothing was running.")

    async def _run_remind(self, arguments: dict) -> str:
        return await reminder_tools.remind(
            self._reminders, self._now(), arguments
        )

    async def _run_list_reminders(self, arguments: dict) -> str:
        return await reminder_tools.listed(self._reminders, self._now())

    async def _run_cancel_reminder(self, arguments: dict) -> str:
        return await reminder_tools.cancel(self._reminders, arguments)

