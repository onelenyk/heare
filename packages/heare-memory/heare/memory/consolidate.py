"""One pass over the journal: what was heard becomes proposed facts.

The journal is the truth about what happened, but a sentence is not a fact
yet. This reads only ``heard`` events, recognises a handful of plain
statements about the owner, and proposes each as a pending fact. It never
confirms — the owner does that elsewhere. A pass is safe to repeat because
``FactStore.has_source`` remembers the event a fact came from, and the
optional cursor only narrows the work: a failed pass leaves the cursor
unmoved, so the next one reads the same lines again.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

from heare.memory.contracts import (
    CONSOLIDATED,
    PENDING,
    USER_SAID,
    ConsolidationReport,
    Fact,
    FactStore,
)
from heare.memory.journal import Journal

_TRAILING = " \t\r\n.,!?;:…"

# The order is the order of precedence: a sentence that matches two
# patterns becomes the first fact, never two.
_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"\bмене\s+звати\s+(?P<value>.+)", re.IGNORECASE),
        "name",
        "Тебе звати {}.",
    ),
    (
        re.compile(r"\bя\s+люблю\s+(?P<value>.+)", re.IGNORECASE),
        "prefers",
        "Ти любиш {}.",
    ),
    (
        re.compile(r"\bмені\s+подобається\s+(?P<value>.+)", re.IGNORECASE),
        "prefers",
        "Ти любиш {}.",
    ),
    (
        re.compile(r"\bя\s+працюю\s+над\s+(?P<value>.+)", re.IGNORECASE),
        "works_on",
        "Ти працюєш над {}.",
    ),
    (
        re.compile(r"\bя\s+живу\s+(?:в|у)\s+(?P<value>.+)", re.IGNORECASE),
        "lives_in",
        "Ти живеш у {}.",
    ),
    (
        re.compile(r"\bне\s+забути\s+(?P<value>.+)", re.IGNORECASE),
        "commitment",
        "Не забути: {}.",
    ),
)

# The owner's own words, so the fact starts out proposed. A pass never
# confirms anything: confirmation is the owner's decision, not ours.
_CONFIDENCE = 0.8


def consolidate(
    store: FactStore,
    journal: Journal,
    *,
    now: float | None = None,
    state_path: Path | None = None,
) -> ConsolidationReport:
    moment = time.time() if now is None else now
    considered = added = skipped = 0
    known = {
        (fact.predicate.casefold(), fact.value.casefold())
        for fact in store.current(include_pending=True)
    }
    if state_path is None:
        events = list(journal.events())
        state = None
    else:
        events, state = journal.new_events(state_path)
    for event in events:
        if event.get("kind") != "heard":
            continue
        considered += 1
        payload = event.get("payload")
        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str):
            continue
        extracted = _extract(text)
        if extracted is None:
            continue
        predicate, value, sentence = extracted
        ident = event["id"]
        if (predicate.casefold(), value.casefold()) in known:
            skipped += 1
            continue
        if store.has_source(ident):
            skipped += 1
            continue
        store.add(_fact(event, predicate, value, sentence, moment))
        known.add((predicate.casefold(), value.casefold()))
        added += 1
    if state_path is not None and state is not None:
        journal.save_state(state_path, state)
    return ConsolidationReport(
        considered=considered, added=added, skipped=skipped
    )


def _extract(text: str) -> tuple[str, str, str] | None:
    """The first rule that matches, with its value trimmed. A value that
    is only punctuation or too long is not a fact worth keeping."""
    for pattern, predicate, template in _RULES:
        found = pattern.search(text)
        if found is None:
            continue
        value = found.group("value").strip().rstrip(_TRAILING).strip()
        if not 2 <= len(value) <= 200:
            return None
        return predicate, value, template.format(value)
    return None


def _fact(
    event: dict, predicate: str, value: str, sentence: str, moment: float
) -> Fact:
    return Fact(
        subject="owner",
        predicate=predicate,
        value=value,
        text=sentence,
        source=CONSOLIDATED,
        trust=USER_SAID,
        status=PENDING,
        confidence=_CONFIDENCE,
        valid_from=_moment(event, moment),
        source_ids=(event["id"],),
        created_at=moment,
    )


def _moment(event: dict, fallback: float) -> float:
    """The event's own time, so a fact does not claim to be newer than the
    words it came from. A line without a readable ``at`` falls back to the
    pass's clock."""
    raw = event.get("at")
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw).timestamp()
        except ValueError:
            return fallback
    return fallback
