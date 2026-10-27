"""Reminders: one-off notes, set, listed, and cancelled by id."""

from __future__ import annotations

import time
from datetime import datetime

from heare.tools.ports import Reminders

# A reminder is a one-off, not a schedule: it waits at most a month, and
# after it is due it is still worth saying for a week.
REMIND_MAX_S = 30 * 24 * 3600
REMIND_GRACE_S = 7 * 24 * 3600


async def remind(store: Reminders, now: float, arguments: dict) -> str:
    text = str(arguments.get("text", "")).strip()
    if not text:
        return "Що саме нагадати?"
    at = str(arguments.get("at") or "").strip()
    if at:
        try:
            moment = datetime.fromisoformat(at)
        except ValueError:
            return "Скажи, коли нагадати."
        if moment.tzinfo is None:
            # A naive time is local wall time: mktime reads the zone rules for
            # that date, so a reminder still lands where the clock says after
            # the clocks change — a fixed offset would drift by an hour.
            due = time.mktime(moment.timetuple()) + moment.microsecond / 1_000_000
        else:
            due = moment.timestamp()
        if due <= now:
            return "Це вже минуло."
        if due - now > REMIND_MAX_S:
            return "Так далеко я не нагадую."
    else:
        try:
            seconds = int(arguments.get("in_seconds"))
        except (TypeError, ValueError):
            return "Скажи, коли нагадати."
        if seconds <= 0:
            return "Це вже минуло."
        if seconds > REMIND_MAX_S:
            return "Так далеко я не нагадую."
        due = now + seconds
    store.hold(
        what=text[:200], why="reminder", weight=3, about="reminder",
        not_before=due, until=due + REMIND_GRACE_S,
    )
    return "Добре, нагадаю."


async def listed(store: Reminders, now: float) -> str:
    items = store.reminders(now)
    if not items:
        return "No reminders."
    lines = []
    for intent in items:
        if intent.not_before is None:
            when = "no time"
        else:
            delta = int(intent.not_before - now)
            if delta <= 0:
                when = "due now"
            elif delta < 3600:
                when = f"in {max(1, delta // 60)} min"
            elif delta < 86400:
                when = f"in {delta // 3600} h"
            else:
                when = f"in {delta // 86400} d"
        lines.append(f"{intent.id}. {intent.what} — {when}")
    return "Reminders:\n" + "\n".join(lines)


async def cancel(store: Reminders, arguments: dict) -> str:
    try:
        intent_id = int(arguments.get("id"))
    except (TypeError, ValueError):
        return "Скажи, яке нагадування скасувати."
    if store.cancel(intent_id):
        return "Скасував."
    return "Не знайшов такого нагадування."
