"""Auto-extract memories from conversation text using regex heuristics.

Zero LLM calls — pure regex pattern matching. Extracts facts, preferences,
decisions, and events from user utterances and agent inferences. Designed
to run as a fire-and-forget background task after each turn.
"""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

from src.memory.base import MemoryEntry, MemoryType

if TYPE_CHECKING:
    from src.memory.base import MemoryBackend

# ── Extraction patterns ──────────────────────────────────────────────
# Each pattern is (regex, memory_type). The first capture group is the value.

_PATTERNS_UK: list[tuple[re.Pattern, MemoryType]] = [
    # Name / identity
    (
        re.compile(r"мене звати\s+(\S[\s\S]{0,40}?)[.!?]?\s*$", re.IGNORECASE),
        MemoryType.FACT,
    ),
    (
        re.compile(r"мене кличуть\s+(\S[\s\S]{0,40}?)[.!?]?\s*$", re.IGNORECASE),
        MemoryType.FACT,
    ),
    # NOT a bare `я (.*)`: that pattern turned every sentence containing
    # «я» — which in spoken Ukrainian is most of them, hallucinations
    # included — into a stored fact about the user. It produced 154 of
    # the 155 memories on this deployment, nearly all junk («че», «бать»,
    # fragments of songs off the TV), three of which were injected into
    # every prompt. Identity statements only.
    (
        re.compile(
            r"я\s+(?:працюю|живу|навчаюсь|навчаюся|розробляю|пишу|роблю)"
            r"\s+(\S[\s\S]{0,60}?)[.!?]?\s*$",
            re.IGNORECASE,
        ),
        MemoryType.FACT,
    ),
    # Preferences
    (
        re.compile(
            r"я\s+(?:люблю|обожнюю|полюбляю)\s+(\S[\s\S]{0,60}?)[.!?]?\s*$",
            re.IGNORECASE,
        ),
        MemoryType.PREFERENCE,
    ),
    (
        re.compile(
            r"мені\s+(?:подобається|до вподоби)\s+(\S[\s\S]{0,60}?)[.!?]?\s*$",
            re.IGNORECASE,
        ),
        MemoryType.PREFERENCE,
    ),
    (
        re.compile(
            r"мій\s+(?:улюблений|улюблена|улюблене)\s+(\S[\s\S]{0,40}?)[.!?]?\s*$",
            re.IGNORECASE,
        ),
        MemoryType.PREFERENCE,
    ),
    # Decisions
    (
        re.compile(
            r"я\s+(?:вирішив|вирішила|вирішили)\s+(\S[\s\S]{0,60}?)[.!?]?\s*$",
            re.IGNORECASE,
        ),
        MemoryType.DECISION,
    ),
    (
        re.compile(
            r"(?:домовились|вирішено|зроблено)\s*[:—–-]?\s*(\S[\s\S]{0,60}?)[.!?]?\s*$",
            re.IGNORECASE,
        ),
        MemoryType.DECISION,
    ),
    # Location
    (
        re.compile(
            r"я\s+(?:живу|мешкаю|знаходжусь)\s+(?:в|у)\s+(\S[\s\S]{0,60}?)[.!?]?\s*$",
            re.IGNORECASE,
        ),
        MemoryType.FACT,
    ),
    # Job / role
    (
        re.compile(r"я\s+(?:працюю|є)\s+(\S[\s\S]{0,60}?)[.!?]?\s*$", re.IGNORECASE),
        MemoryType.FACT,
    ),
]

_PATTERNS_EN: list[tuple[re.Pattern, MemoryType]] = [
    (
        re.compile(r"my name is\s+(\S[\s\S]{0,40}?)[.!?]?\s*$", re.IGNORECASE),
        MemoryType.FACT,
    ),
    (
        re.compile(r"call me\s+(\S[\s\S]{0,40}?)[.!?]?\s*$", re.IGNORECASE),
        MemoryType.FACT,
    ),
    (
        re.compile(r"i(?:'m| am)\s+(\S[\s\S]{0,40}?)[.!?]?\s*$", re.IGNORECASE),
        MemoryType.FACT,
    ),
    (
        re.compile(
            r"i\s+(?:like|love|enjoy|prefer)\s+(\S[\s\S]{0,60}?)[.!?]?\s*$",
            re.IGNORECASE,
        ),
        MemoryType.PREFERENCE,
    ),
    (
        re.compile(
            r"my (?:favourite|favorite)\s+\w+\s+is\s+(\S[\s\S]{0,40}?)[.!?]?\s*$",
            re.IGNORECASE,
        ),
        MemoryType.PREFERENCE,
    ),
    (
        re.compile(
            r"i\s+(?:decided|chose|picked)\s+(\S[\s\S]{0,60}?)[.!?]?\s*$", re.IGNORECASE
        ),
        MemoryType.DECISION,
    ),
    (
        re.compile(
            r"i\s+(?:live|reside)\s+(?:in|at)\s+(\S[\s\S]{0,60}?)[.!?]?\s*$",
            re.IGNORECASE,
        ),
        MemoryType.FACT,
    ),
    (
        re.compile(
            r"i\s+(?:work|am)\s+(?:as|a|an)\s+(\S[\s\S]{0,60}?)[.!?]?\s*$",
            re.IGNORECASE,
        ),
        MemoryType.FACT,
    ),
]


def _normalize_content(text: str) -> str:
    """Strip diacritics, lowercase, collapse whitespace."""
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(stripped.lower().split())


def _content_fingerprint(text: str) -> str:
    """Fingerprint for dedup — removes stopwords and punctuation."""
    _STOP = frozenset({"the", "a", "an", "is", "in", "at", "on", "of", "to", "and"})
    clean = _normalize_content(text)
    tokens = [t.strip(".,!?;:") for t in clean.split() if t not in _STOP and len(t) > 1]
    return " ".join(tokens)


def extract_memories(text: str) -> list[MemoryEntry]:
    """Extract MemoryEntry objects from a single text string.

    Tests all UK + EN patterns. Returns empty list if nothing matches.
    Each match becomes a MemoryEntry with source="auto_extracted".
    """
    results: list[MemoryEntry] = []
    seen: set[str] = set()

    for patterns in (_PATTERNS_UK, _PATTERNS_EN):
        for pat, mem_type in patterns:
            m = pat.search(text)
            if not m:
                continue
            value = m.group(1).strip().rstrip(".!?,;:")
            if not value or len(value) < 2:
                continue
            # Dedup within this extraction batch
            dedup_key = f"{mem_type.value}:{value.lower()}"
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            results.append(
                MemoryEntry(
                    id="",
                    type=mem_type,
                    content=value,
                    source="auto_extracted",
                    confidence=0.7,  # lower confidence for auto-extracted
                )
            )

    if len(results) > 1:
        results = _dedup_by_containment(results)
    return results


_TYPE_PRIORITY: dict[MemoryType, int] = {
    MemoryType.DECISION: 3,
    MemoryType.PREFERENCE: 2,
    MemoryType.FACT: 1,
    MemoryType.EVENT: 1,
}


def _dedup_by_containment(entries: list[MemoryEntry]) -> list[MemoryEntry]:
    """Remove entries whose content is a substring of another entry.

    When a generic pattern and a specific pattern both match the same
    text, keeps the more specific type and longer content.
    """
    ranked = sorted(
        entries,
        key=lambda e: (_TYPE_PRIORITY.get(e.type, 0), len(e.content)),
        reverse=True,
    )
    out: list[MemoryEntry] = []
    seen: set[str] = set()
    for entry in ranked:
        norm = _normalize_content(entry.content)
        if any(norm in s for s in seen):
            continue
        if any(s in norm for s in seen):
            continue
        seen.add(norm)
        out.append(entry)
    return out


def _worth_keeping(content: str) -> bool:
    """A last gate before a fragment becomes a fact about the user.

    Speech recognition invents phrases in silence and a regex cannot
    tell «я не можу зробити бобів» from a preference. Anything the
    hallucination filter recognises, and anything too short to be a
    statement, is not remembered.
    """
    text = (content or "").strip()
    words = text.split()
    # A name is the most valuable thing to remember and the shortest
    # thing said: «мене звати Назар» must survive a length rule written
    # for sentences. Only the specific name patterns can produce a bare
    # capitalised token now that the catch-all «я (.*)» is gone.
    is_name = 1 <= len(words) <= 2 and all(
        w[:1].isupper() and w.replace("-", "").isalpha() for w in words
    )
    if not is_name and len(words) < 3:
        return False
    try:
        from src.spine.hallucinations import is_junk

        return not is_junk(text, agent_spoke_recently=True)
    except Exception:  # the filter is optional, the length rule is not
        return True


async def extract_and_store(backend: "MemoryBackend", text: str) -> int:
    """Extract memories from text and store them via the backend.

    Checks for similar existing entries before storing using both
    content search and fingerprint comparison.  Returns number of new
    memories stored.
    """
    extracted = [e for e in extract_memories(text) if _worth_keeping(e.content)]
    if not extracted:
        return 0

    stored = 0
    for entry in extracted:
        existing = await backend.search(entry.content, limit=5)
        if existing:
            fp_new = _content_fingerprint(entry.content)
            for ex in existing:
                if _content_fingerprint(ex.content) == fp_new:
                    break
            else:
                await backend.store(entry)
                stored += 1
        else:
            await backend.store(entry)
            stored += 1

    return stored


__all__ = ["extract_memories", "extract_and_store"]
