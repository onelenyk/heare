"""Where the intents wait: a small file on disk that survives a restart.

Forgiving on purpose. The shape of an intent is a first guess and will
change; a file that only understands today's fields would turn every change
into a migration. So what is read is what the file has — unknown fields are
dropped, missing ones take their defaults.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path

from heare.engine.intent import ANSWERS, REMINDER, Intent

logger = logging.getLogger("heare.engine.store")

_KNOWN = {f.name for f in fields(Intent)}
_NUMBER = (int, float)


def _sound(row: object) -> bool:
    """A row the store can use: right container, right types, nothing sortable broken."""
    if not isinstance(row, dict):
        return False
    if not isinstance(row.get("id"), int) or isinstance(row.get("id"), bool):
        return False
    if not isinstance(row.get("what"), str) or not isinstance(row.get("why"), str):
        return False
    if not isinstance(row.get("weight"), int) or isinstance(row.get("weight"), bool):
        return False
    for name in ("until", "not_before", "made", "delivered_at"):
        value = row.get(name)
        if value is not None and (not isinstance(value, _NUMBER) or isinstance(value, bool)):
            return False
    for name in ("about", "status"):
        if name in row and not isinstance(row[name], str):
            return False
    return True


class Intents:
    """The notes the engine holds, kept on disk between runs."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._by_id: dict[int, Intent] = {}
        self._next = 1
        self._load()

    def _load(self) -> None:
        try:
            rows = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rows = []
        for row in rows if isinstance(rows, list) else []:
            if not _sound(row):
                # A corrupt row must cost one intent, never the brain.
                logger.warning("skipping a corrupt intent row: %.100r", row)
                continue
            intent = Intent(**{k: v for k, v in row.items() if k in _KNOWN})
            self._by_id[intent.id] = intent
            self._next = max(self._next, intent.id + 1)

    def _save(self) -> None:
        rows = [asdict(i) for i in self._by_id.values()]
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, self._path)  # atomic: a crash never leaves half a file

    def hold(self, what: str, why: str, weight: int,
             about: str = "", until: float | None = None,
             not_before: float | None = None) -> Intent:
        intent = Intent(id=self._next, what=what, why=why, weight=weight,
                        about=about, until=until, not_before=not_before)
        self._next += 1
        self._by_id[intent.id] = intent
        self._save()
        return intent

    def alive(self, now: float) -> list[Intent]:
        """What is still worth saying — the «Тримаю в голові» list."""
        return [i for i in self._by_id.values() if i.alive(now)]

    def get(self, intent_id: int) -> Intent | None:
        return self._by_id.get(intent_id)

    def reminders(self, now: float) -> list[Intent]:
        """Held reminders, due or still ahead, soonest first."""
        held = [
            i for i in self._by_id.values()
            if i.why == REMINDER and i.status == "held"
            and (i.until is None or now < i.until)
        ]
        return sorted(held, key=lambda i: i.not_before or 0)

    def cancel(self, intent_id: int, *, why: str = REMINDER) -> bool:
        """Drop a held note of this kind; False if it is not there to drop."""
        intent = self._by_id.get(intent_id)
        if intent is None or intent.why != why or intent.status != "held":
            return False
        intent.status = "dropped"
        self._save()
        return True

    def done(self, intent: Intent, status: str = "delivered",
             *, at: float | None = None) -> None:
        intent.status = status
        if status == "delivered" and at is not None:
            intent.delivered_at = at
        self._save()

    def news_delivered(self, now: float) -> int:
        """How many unsolicited news intents were told on this local day."""
        day = datetime.fromtimestamp(now).date()
        return sum(
            1 for i in self._by_id.values()
            if i.status == "delivered"
            and i.why not in ANSWERS
            and i.why != REMINDER
            and i.delivered_at is not None
            and datetime.fromtimestamp(i.delivered_at).date() == day
        )
