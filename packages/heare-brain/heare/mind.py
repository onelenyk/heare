"""The owner's mind, as the door needs it.

The door may not import a part, so the brain hands it this small surface
instead. It reads memory's stores and owns the few rules the page relies
on: what counts as pending, how much a profile block holds, and when a
write is malformed.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from heare.config import Settings
from heare.engine import Intent, Intents
from heare.memory.contracts import PENDING, Fact, FactStore
from heare.memory.profile import (
    GOALS_LIMIT,
    THREADS_LIMIT,
    USER_LIMIT,
    Profile,
)
from heare.memory.review import last_reviewed

if TYPE_CHECKING:
    from heare.memory.book import Book
    from heare.memory.journal import Journal

LIMITS = {"user": USER_LIMIT, "threads": THREADS_LIMIT, "goals": GOALS_LIMIT}
# The page shows a living sample, not an archive. Counts stay full.
RECENT = 50
# An event id is what Journal.append mints: ev_ and 32 hex digits.
_EVENT_ID = re.compile(r"ev_[0-9a-f]{32}")
FORGET_LIMIT = 50

_JOURNAL_TITLES = {
    "heard": "почуто",
    "said": "сказано",
    "task": "справа",
    "verb": "дія",
    "permission": "дозвіл",
    "permission_closed": "дозвіл закрито",
    "forget": "забуто",
    "profile_edited": "профіль",
}
_JOURNAL_TEXT_KEYS = ("text", "what", "name", "key", "status")
_JOURNAL_META_KEYS = ("channel", "status")


def _fact_view(fact: Fact) -> dict[str, Any]:
    return {
        "id": fact.id,
        "text": fact.text,
        "predicate": fact.predicate,
        "value": fact.value,
        "source": fact.source,
        "trust": fact.trust,
        "confidence": fact.confidence,
        "valid_from": fact.valid_from,
        "created_at": fact.created_at,
        "status": fact.status,
    }


def _reminder_view(intent: Intent) -> dict[str, Any]:
    return {
        "id": intent.id,
        "what": intent.what,
        "about": intent.about,
        "not_before": intent.not_before,
        "until": intent.until,
        "made": intent.made,
        "weight": intent.weight,
    }


def _consolidated(root: Path) -> str | None:
    try:
        moment = (root / "consolidate.json").stat().st_mtime
        return datetime.fromtimestamp(moment).replace(microsecond=0).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _payload(event: dict) -> dict:
    found = event.get("payload")
    return found if isinstance(found, dict) else {}


def _event_text(event: dict) -> str:
    payload = _payload(event)
    for key in _JOURNAL_TEXT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _event_meta(event: dict) -> list[str]:
    meta: list[str] = []
    payload = _payload(event)
    for value in (event.get("source"), event.get("trust")):
        if isinstance(value, str) and value.strip() and value not in meta:
            meta.append(value)
    for key in _JOURNAL_META_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip() and value not in meta:
            meta.append(value)
    return meta


def _event_view(event: dict) -> dict[str, Any]:
    kind = event.get("kind")
    raw_kind = kind if isinstance(kind, str) else ""
    at = event.get("at")
    return {
        "id": str(event.get("id", "")),
        "at": at if isinstance(at, str) else "",
        "kind": raw_kind,
        "title": _JOURNAL_TITLES.get(raw_kind, raw_kind),
        "text": _event_text(event),
        "meta": _event_meta(event),
    }


class Mind:
    """What the page may know and change about the owner's memory."""

    def __init__(self, facts: FactStore, intents: Intents,
                 settings: Settings, *, journal: Journal | None = None,
                 memory: Book | None = None) -> None:
        self._facts = facts
        self._intents = intents
        self._settings = settings
        self._journal = journal
        self._memory = memory

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        pending = [f for f in self._facts.current(include_pending=True)
                   if f.status == PENDING]
        confirmed = self._facts.current()
        held = self._intents.reminders(now)
        root = Path(self._settings.memory_dir)
        last = last_reviewed(root)
        return {
            "counts": {
                "pending": len(pending),
                "confirmed": len(confirmed),
                "reminders": len(held),
            },
            "facts": {
                "pending": [_fact_view(f) for f in pending[:RECENT]],
                "confirmed": [_fact_view(f) for f in confirmed[:RECENT]],
            },
            "reminders": [_reminder_view(i) for i in held],
            "review": {
                "last": last.isoformat() if last is not None else None,
                "hour": int(self._settings.review_hour),
            },
            "consolidation": {"last": _consolidated(root)},
            "profile": {
                "blocks": Profile(root).blocks,
                "limits": dict(LIMITS),
            },
        }

    def journal(self, limit: int = 50) -> dict[str, Any]:
        if self._journal is None:
            raise RuntimeError("memory is not open here")
        return {
            "items": [_event_view(event) for event in self._journal.latest(limit)]
        }

    async def search(self, query: str, when: str = "") -> dict[str, Any]:
        if self._memory is None:
            raise RuntimeError("memory is not open here")
        said, items = await self._memory.look_up(query, when)
        return {"said": said, "items": items}

    async def forget(self, ids: object) -> int:
        if self._memory is None:
            raise RuntimeError("memory is not open here")
        if not isinstance(ids, list) or not ids:
            raise ValueError("ids must be a list of journal event ids")
        if len(ids) > FORGET_LIMIT:
            raise ValueError("too many ids at once")
        checked = [i for i in ids if isinstance(i, str) and _EVENT_ID.fullmatch(i)]
        if len(checked) != len(ids):
            raise ValueError("ids must be a list of journal event ids")
        return await self._memory.forget(checked)

    def confirm(self, fact_id: int) -> bool:
        return bool(self._facts.confirm(fact_id))

    def reject(self, fact_id: int) -> bool:
        return bool(self._facts.reject(fact_id))

    def cancel(self, intent_id: int) -> bool:
        return bool(self._intents.cancel(intent_id))

    def save_profile(self, key: object, text: object) -> None:
        """Write one block, or raise ValueError for something the page sent."""
        if not isinstance(key, str) or key not in LIMITS:
            raise ValueError("unknown profile block")
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        if len(text.strip()) > LIMITS[key]:
            raise ValueError("too long for this block")
        Profile(Path(self._settings.memory_dir)).write(key, text)
